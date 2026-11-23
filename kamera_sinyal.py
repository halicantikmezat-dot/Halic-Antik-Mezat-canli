"""Diğer - Her Şey vitrini için WebRTC sinyal köprüsü. Medya sunucudan geçmez."""

_DUKKAN = "diger"
_yayinci = ""


def bagla(socketio, admin_mi):
    from flask import request

    def _admin():
        try:
            return bool(admin_mi())
        except Exception:
            return False

    @socketio.on("kamera_yayin_basla")
    def kamera_yayin_basla(_veri=None):
        global _yayinci
        if not _admin():
            return
        _yayinci = request.sid
        socketio.emit("kamera_yayin_var", {"dukkan": _DUKKAN}, room=_DUKKAN)

    @socketio.on("kamera_yayin_bitti")
    def kamera_yayin_bitti(_veri=None):
        global _yayinci
        if request.sid != _yayinci:
            return
        _yayinci = ""
        socketio.emit("kamera_yayin_bitti", {"dukkan": _DUKKAN}, room=_DUKKAN)

    @socketio.on("kamera_izle")
    def kamera_izle(veri=None):
        gelen = veri or {}
        if str(gelen.get("dukkan") or _DUKKAN) != _DUKKAN:
            return
        if not _yayinci or _yayinci == request.sid:
            return
        socketio.emit("kamera_izleyici", {"sid": request.sid, "dukkan": _DUKKAN}, room=_yayinci)

    @socketio.on("kamera_teklif")
    def kamera_teklif(veri=None):
        gelen = veri or {}
        hedef = str(gelen.get("hedef") or "")
        if request.sid != _yayinci or not hedef or not isinstance(gelen.get("sdp"), dict):
            return
        socketio.emit("kamera_teklif", {"sdp": gelen.get("sdp"), "yayinci": request.sid}, room=hedef)

    @socketio.on("kamera_yanit")
    def kamera_yanit(veri=None):
        gelen = veri or {}
        if not _yayinci or not isinstance(gelen.get("sdp"), dict):
            return
        socketio.emit("kamera_yanit", {"sdp": gelen.get("sdp"), "sid": request.sid}, room=_yayinci)

    @socketio.on("kamera_buz")
    def kamera_buz(veri=None):
        gelen = veri or {}
        hedef = str(gelen.get("hedef") or "")
        if not hedef or not isinstance(gelen.get("candidate"), dict):
            return
        if request.sid != _yayinci and hedef != _yayinci:
            return
        socketio.emit("kamera_buz", {"candidate": gelen.get("candidate"), "sid": request.sid}, room=hedef)

    @socketio.on("disconnect")
    def kamera_kop():
        global _yayinci
        sid = request.sid
        if sid and sid == _yayinci:
            _yayinci = ""
            socketio.emit("kamera_yayin_bitti", {"dukkan": _DUKKAN}, room=_DUKKAN)
            return
        if sid and _yayinci:
            socketio.emit("kamera_izleyici_ayrildi", {"sid": sid}, room=_yayinci)
