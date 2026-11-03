"""Common backend interface: load once, generate, count calls. No heavy imports at module level."""
import time
from release import canonical, digest


class StubBackend:
    def __init__(self, manifest, artifact=None):
        self.loaded_id = manifest['model_id']
        self.runtime_sha256 = manifest['runtime_sha256']
        self.calls = 0

    def generate(self, prompt, limit):
        self.calls += 1
        time.sleep(.01)
        return {'answer': f'STUB ONLY: {self.loaded_id}', 'output_tokens': 1}


class TransformersBackend:
    def __init__(self, manifest, artifact):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        self.loaded_id = manifest['model_id']
        self.runtime_sha256 = manifest['runtime_sha256']
        self.runtime = manifest['runtime']
        self.calls = 0
        self.torch = torch
        torch.set_num_threads(4)
        torch.manual_seed(self.runtime['generation'].get('seed', 42))
        self.device = self.runtime.get('device', 'cpu')
        if self.runtime['dtype'] not in ('float32', 'bfloat16', 'float16'):
            raise ValueError('Unsupported dtype')
        base = artifact / self.runtime.get('base_subdir', '.')
        tokenizer_path = artifact / self.runtime.get('tokenizer_subdir', '.')
        self.tokenizer = AutoTokenizer.from_pretrained(tokenizer_path, local_files_only=True)
        template = self.tokenizer.chat_template
        if digest(canonical(template)) != self.runtime['template_sha256']:
            raise ValueError('Loaded chat template mismatch')
        self.model = AutoModelForCausalLM.from_pretrained(
            base, local_files_only=True, dtype=getattr(torch, self.runtime['dtype']))
        if manifest.get('kind') == 'adapter':
            from peft import PeftModel
            self.model = PeftModel.from_pretrained(self.model, artifact / 'adapter', local_files_only=True)
        self.model = self.model.to(self.device).eval()

    def validate_prompt(self, prompt):
        g = self.runtime['generation']
        text = self.tokenizer.apply_chat_template(
            [{'role': 'system', 'content': g['system']}, {'role': 'user', 'content': prompt}],
            tokenize=False, add_generation_prompt=True, enable_thinking=g['enable_thinking'])
        if len(self.tokenizer(text)['input_ids']) > self.runtime['max_input_tokens']:
            raise ValueError('Tokenized input exceeds supported limit')

    def generate(self, prompt, limit):
        self.calls += 1
        g = self.runtime['generation']
        messages = [{'role': 'system', 'content': g['system']}, {'role': 'user', 'content': prompt}]
        text = self.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True,
                                                  enable_thinking=g['enable_thinking'])
        batch = self.tokenizer(text, return_tensors='pt').to(self.device)
        if batch['input_ids'].shape[1] > self.runtime['max_input_tokens']:
            raise ValueError('Tokenized input exceeds supported limit')
        with self.torch.inference_mode():
            out = self.model.generate(**batch, max_new_tokens=limit, do_sample=False,
                                     pad_token_id=self.tokenizer.pad_token_id or self.tokenizer.eos_token_id)
        tokens = out[0, batch['input_ids'].shape[1]:]
        return {'answer': self.tokenizer.decode(tokens, skip_special_tokens=True), 'output_tokens': len(tokens)}
