"""Keep local API/Prefect traffic off inherited environment and Windows proxies."""
import os


def configure_loopback():
    hosts = []
    for name in ('NO_PROXY', 'no_proxy'):
        for host in os.environ.get(name, '').split(','):
            host = host.strip()
            if host and host not in hosts:
                hosts.append(host)
    for host in ('127.0.0.1', 'localhost', '::1'):
        if host not in hosts:
            hosts.append(host)
    value = ','.join(hosts)
    os.environ['NO_PROXY'] = value
    os.environ['no_proxy'] = value
    return value
