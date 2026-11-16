var PWA_ONBELLEK = "halic-antik-pwa-v1";
var PWA_DOSYALAR = [
    "/static/img/icon-192.png",
    "/static/img/icon-512.png",
    "/manifest.json"
];

function pwaDogrudanAg(request) {
    if (!request || request.method !== "GET") return true;
    var url;
    try {
        url = new URL(request.url);
    } catch (e) {
        return true;
    }
    var yol = url.pathname || "";
    var adres = url.href || "";
    if (yol.indexOf("/socket.io") !== -1 || adres.indexOf("/socket.io") !== -1) return true;
    if (yol === "/api" || yol.indexOf("/api/") === 0) return true;
    if (/\.m3u8($|\?)/i.test(adres) || /\.ts($|\?)/i.test(adres)) return true;
    return false;
}

function pwaOnbellekDosyasi(request) {
    try {
        var yol = new URL(request.url).pathname;
        return PWA_DOSYALAR.indexOf(yol) !== -1;
    } catch (e) {
        return false;
    }
}

self.addEventListener("install", function (event) {
    event.waitUntil(
        caches.open(PWA_ONBELLEK).then(function (cache) {
            return cache.addAll(PWA_DOSYALAR);
        }).then(function () {
            return self.skipWaiting();
        })
    );
});

self.addEventListener("activate", function (event) {
    event.waitUntil(
        caches.keys().then(function (anahtarlar) {
            return Promise.all(anahtarlar.map(function (anahtar) {
                if (anahtar !== PWA_ONBELLEK) return caches.delete(anahtar);
                return Promise.resolve();
            }));
        }).then(function () {
            return self.clients.claim();
        })
    );
});

self.addEventListener("fetch", function (event) {
    var request = event.request;
    if (pwaDogrudanAg(request)) return;

    event.respondWith(
        fetch(request).then(function (response) {
            if (response && response.ok && pwaOnbellekDosyasi(request)) {
                var kopya = response.clone();
                caches.open(PWA_ONBELLEK).then(function (cache) {
                    return cache.put(request, kopya);
                }).catch(function () {});
            }
            return response;
        }).catch(function () {
            return caches.match(request);
        })
    );
});
