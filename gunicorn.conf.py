import os

# Dinleme soketi, uygulama modulu yuklenmeden acilir.
# Render start komutunda --preload verilmemelidir.
preload_app = False


def _tam_sayi(ad, varsayilan):
    try:
        return int(os.environ.get(ad, varsayilan))
    except (TypeError, ValueError):
        return int(varsayilan)


# Sahne sozlugu ve Socket.IO odalari yalniz bu surecte yasar.
# WEB_CONCURRENCY sureci cogaltip yonetici ile izleyiciyi ayri belleklere dusurmesin.
workers = 1
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

    Uygulama yuklenmeden gevent yamasi vurulur. eventlet secilmez;
    thread ve contextvars acik kalir ki arka plan yesil ipligi
    SQLAlchemy app_context kaydini uyku sirasinda dusurmesin.
    """
    import sys
    from gevent import monkey

    ssl_onceden = "ssl" in sys.modules
    monkey.patch_all(thread=True, contextvars=True, ssl=not ssl_onceden)
    os.environ["GUNICORN_WORKER_PID"] = str(worker.pid)
    worker.log.info(
        "worker %s gevent baglami hazir ssl=%s",
        worker.pid,
        "0" if ssl_onceden else "1",
    )
