from core import Settings, Store

if __name__ == '__main__':
    count = Store(Settings.from_env()).retry()
    print(f'Retried {count} pending or known-failed messages. Inspect /admin for results.')
    print('Unknown and interrupted sends are left untouched; check provider logs before reconciliation.')
