import os

# Dinleme soketi, uygulama modulu yuklenmeden acilir.
# Render start komutunda --preload verilmemelidir.
preload_app = False


def _tam_sayi(ad, varsayilan):
    try:
        return int(os.environ.get(ad, varsayilan))
    except (TypeError, ValueError):
        return int(varsayilan)


# Oda belleği ve Socket.IO tek süreçte kalır. WEB_CONCURRENCY verilirse o kullanılır.
workers = _tam_sayi("WEB_CONCURRENCY", 1)
worker_class = "geventwebsocket.gunicorn.workers.GeventWebSocketWorker"
worker_connections = _tam_sayi("GUNICORN_WORKER_CONNECTIONS", 2000)
# 0: canlı sayaç sırasında işçi kendini yeniden başlatmaz.
max_requests = 0
max_requests_jitter = 250
timeout = 120
graceful_timeout = 30
keepalive = 5


def post_fork(server, worker):
    """Sema ve indeks DDL'i burada calismaz.

    preload_app kapali oldugu icin her worker uygulamayi kendi yukler.
    PostgreSQL advisory lock app acilisindadir. Bu kanca yalnizca worker
    kimligini isaretler.
    """
    os.environ["GUNICORN_WORKER_PID"] = str(worker.pid)
    worker.log.info("worker %s acildi; sema kilidi boot asamasinda", worker.pid)
