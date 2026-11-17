import os

# Dinleme soketi, uygulama modulu yuklenmeden acilir.
# Render start komutunda --preload verilmemelidir.
preload_app = False


def _tam_sayi(ad, varsayilan):
    try:
        return int(os.environ.get(ad, varsayilan))
    except (TypeError, ValueError):
        return int(varsayilan)


# Render 1 CPU / 2 GB. WEB_CONCURRENCY varsa o kullanilir.
workers = _tam_sayi("WEB_CONCURRENCY", 3)
worker_class = "geventwebsocket.gunicorn.workers.GeventWebSocketWorker"
worker_connections = 1000
max_requests = 2000
max_requests_jitter = 250
timeout = 120
keepalive = 5


def post_fork(server, worker):
    """Sema ve indeks DDL'i burada calismaz.

    preload_app kapali oldugu icin her worker uygulamayi kendi yukler.
    PostgreSQL advisory lock app acilisindadir; uc worker ayni anda ALTER
    calistirmaz. Bu kanca yalnizca worker kimligini isaretler.
    """
    os.environ["GUNICORN_WORKER_PID"] = str(worker.pid)
    worker.log.info("worker %s acildi; sema kilidi boot asamasinda", worker.pid)
