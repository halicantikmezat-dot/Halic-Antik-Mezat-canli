(function () {
    var davetKodu = "";
    var armaHarita = [];
    var seritKuyruk = [];
    var seritAktif = 0;
    var kazanilan = {};

    function dukkan() {
        return (typeof aktifDukkan === "string" && aktifDukkan) ? aktifDukkan : "diger";
    }

    function kacis(metin) {
        return String(metin == null ? "" : metin)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");
    }

    function ziyaretci() {
        var anahtar = "fanus_ziyaret_v1";
        var ilk = !localStorage.getItem(anahtar);
        var kim = localStorage.getItem(anahtar);
        if (!kim) {
            kim = Math.random().toString(36).slice(2) + Date.now().toString(36);
            localStorage.setItem(anahtar, kim);
        }
        return { kim: kim, ilk: ilk };
    }

    function seritBosalt() {
        var alan = document.getElementById("fanusSeritAlani");
        if (!alan) return;
        while (seritAktif < 2 && seritKuyruk.length) {
            seritAktif += 1;
            var metin = seritKuyruk.shift();
            var serit = document.createElement("div");
            serit.className = "fanus-serit";
            serit.textContent = metin;
            alan.appendChild(serit);
            (function (el) {
                setTimeout(function () {
                    if (el.parentNode) el.parentNode.removeChild(el);
                    seritAktif -= 1;
                    seritBosalt();
                }, 4600);
            })(serit);
        }
    }

    function seritEkle(metin) {
        if (!metin) return;
        seritKuyruk.push(metin);
        seritBosalt();
    }

    function armaBul(kimlik, ad) {
        var i;
        var adTemiz = String(ad || "").replace(/^\[.*?\]\s*/, "").trim().toLocaleLowerCase("tr-TR");
        for (i = 0; i < armaHarita.length; i++) {
            var kayit = armaHarita[i];
            if (kimlik && String(kayit.id) === String(kimlik)) return kayit;
        }
        for (i = 0; i < armaHarita.length; i++) {
            var adKayit = String(armaHarita[i].ad || "").trim().toLocaleLowerCase("tr-TR");
            if (adTemiz && adKayit && (adTemiz === adKayit || adTemiz.indexOf(adKayit) !== -1 || adKayit.indexOf(adTemiz) !== -1)) {
                return armaHarita[i];
            }
        }
        return null;
    }

    function ismeArma(el, kimlik, ad) {
        if (!el) return;
        var arma = armaBul(kimlik, ad);
        if (!arma || !arma.emoji) return;
        var yazi = el.textContent || "";
        if (yazi.indexOf(arma.emoji) === 0) return;
        el.textContent = arma.emoji + " " + yazi;
        el.title = arma.unvan || "";
        if (arma.altin) el.classList.add("fanus-altin-isim");
    }

    function bantCiz(paket) {
        var kutu = document.getElementById("fanusBantlar");
        var not = document.getElementById("fanusNot");
        var baslik = document.getElementById("fanusBaslikArma");
        if (!kutu) return;
        var html = "";
        var bantlar = (paket && paket.bantlar) || [];
        var benim = null;
        bantlar.forEach(function (bant) {
            if (bant.ben) benim = bant;
            var sinif = "fanus-bant" + (bant.ben ? " ben" : "");
            var kimSinif = "fanus-kim" + (bant.altin ? " altin" : "");
            var yuzde = Math.max(0, Math.min(100, Number(bant.yuzde) || 0));
            var etiket = bant.bos
                ? "—"
                : (kacis(bant.emoji) + " " + kacis(bant.ad) + " <span class=\"raunt\">R" + kacis(bant.raunt) + "</span>");
            html += '<div class="' + sinif + '">'
                + '<span class="fanus-sira">' + kacis(bant.sira) + '</span>'
                + '<span class="' + kimSinif + '" title="' + kacis(bant.unvan || "") + '">' + etiket + '</span>'
                + '<span class="fanus-cubuk"><span style="width:' + yuzde + '%;background:' + kacis(bant.renk) + '"></span></span>'
                + '<span class="fanus-yuzde" style="color:' + kacis(bant.renk) + '">%' + yuzde + '</span>'
                + '</div>';
        });
        kutu.innerHTML = html;
        if (not) not.textContent = (paket && paket.not) || "";
        if (baslik) {
            baslik.textContent = benim ? (benim.emoji + " " + benim.unvan) : "";
        }
        davetKodu = (paket && paket.davet_kodu) || "";
        if (davetKodu) localStorage.setItem("fanus_davet_kodum", davetKodu);
    }

    function panoCek() {
        return fetch("/api/fanus/pano?dukkan=" + encodeURIComponent(dukkan()), {
            cache: "no-store",
            credentials: "same-origin"
        }).then(function (r) { return r.json(); }).then(function (paket) {
            bantCiz(paket || {});
        }).catch(function () {});
    }

    function armaCek() {
        return fetch("/api/fanus/armalar?dukkan=" + encodeURIComponent(dukkan()), {
            cache: "no-store",
            credentials: "same-origin"
        }).then(function (r) { return r.json(); }).then(function (paket) {
            armaHarita = (paket && paket.kayitlar) || [];
        }).catch(function () {});
    }

    function davetAdresi() {
        var kok = window.location.origin || "";
        return kok + "/dukkan/" + encodeURIComponent(dukkan()) + "?davet=" + encodeURIComponent(davetKodu);
    }

    function davetKopyala() {
        if (!davetKodu) {
            alert("Davet linki için giriş yapın.");
            return;
        }
        var adres = davetAdresi();
        var bitti = function () { alert("Davet linki kopyalandı."); };
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(adres).then(bitti).catch(function () {
                window.prompt("Linki kopyalayın:", adres);
            });
            return;
        }
        window.prompt("Linki kopyalayın:", adres);
    }

    function davetBildir() {
        var sorgu = new URLSearchParams(window.location.search);
        var kod = (sorgu.get("davet") || "").trim().toLowerCase();
        if (!kod) return;
        if (localStorage.getItem("fanus_davet_kodum") === kod) return;
        var kim = ziyaretci();
        var ad = "";
        try { ad = localStorage.getItem("halic_antik_ad") || ""; } catch (e) { ad = ""; }
        fetch("/api/fanus/davet", {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                kod: kod,
                ziyaretci: kim.kim,
                ilk: kim.ilk,
                dukkan: dukkan(),
                kullanici_id: (typeof kullaniciId !== "undefined" && kullaniciId) ? kullaniciId : null,
                gelen_ad: ad
            })
        }).then(function (r) { return r.json(); }).then(function (paket) {
            if (paket && paket.serit) seritEkle(paket.serit);
        }).catch(function () {});
    }

    function kazanBildir(data) {
        if (!data || data.dukkan && data.dukkan !== dukkan()) return;
        var urun = data.urun_id || data.id;
        var kazanan = data.kazanan_id;
        if (!urun || !kazanan || typeof kullaniciId === "undefined" || String(kazanan) !== String(kullaniciId)) return;
        var anahtar = String(urun);
        if (kazanilan[anahtar]) return;
        kazanilan[anahtar] = true;
        fetch("/api/fanus/kazan", {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                urun_id: urun,
                dukkan: dukkan(),
                kullanici_id: kullaniciId
            })
        }).catch(function () {});
    }

    function soketKur() {
        if (typeof socket === "undefined" || !socket || !socket.on) return;
        socket.on("fanus_tazele", function (data) {
            if (data && data.dukkan && data.dukkan !== dukkan()) return;
            panoCek();
            armaCek();
        });
        socket.on("fanus_serit", function (data) {
            if (!data || (data.dukkan && data.dukkan !== dukkan())) return;
            seritEkle(data.metin);
        });
        socket.on("yeni_canli_yorum", function (data) {
            if (!data || data.dukkan !== dukkan()) return;
            if (data.tur === "tanitim" || data.tur === "anons" || data.tur === "sayac" || data.tip === "sunucu_anons" || data.tip === "ahmet_anons") return;
            var kutu = document.getElementById("izleyiciChatKutusu");
            var son = kutu && kutu.lastElementChild;
            var ad = son && son.querySelector("b");
            ismeArma(ad, data.kullanici_id, data.kullanici);
        });
        socket.on("pey_guncellendi", function (data) {
            if (!data || data.dukkan !== dukkan()) return;
            ismeArma(
                document.getElementById("aktif_kazanan"),
                data.kazanan_id,
                data.ad || data.kazanan
            );
            var ticker = document.getElementById("bidTickerStream");
            var sonPey = ticker && ticker.lastElementChild;
            ismeArma(sonPey, data.kazanan_id, data.ad || data.kazanan);
        });
        socket.on("urun_satildi_erken", kazanBildir);
        socket.on("sayac_bitti", function (data) {
            if (!data) return;
            var satildi = data.satildi_mi !== undefined ? data.satildi_mi : (data.kazanan && data.kazanan !== "Yok");
            if (satildi) kazanBildir(data);
        });
    }

    function yerlestir() {
        var alan = document.getElementById("fanusSeritAlani");
        if (alan && alan.parentNode !== document.body) document.body.appendChild(alan);
        var odul = document.getElementById("fanusOdulBtn");
        var kutu = document.getElementById("fanusOdul");
        if (odul && kutu) {
            odul.addEventListener("click", function () {
                kutu.hidden = !kutu.hidden;
            });
        }
        var davet = document.getElementById("fanusDavetBtn");
        if (davet) davet.addEventListener("click", davetKopyala);
        if (typeof kargoKurallarAc === "function" && !kargoKurallarAc._fanus) {
            var eski = kargoKurallarAc;
            var sarmal = function () {
                eski();
                panoCek();
            };
            sarmal._fanus = true;
            window.kargoKurallarAc = sarmal;
        }
    }

    yerlestir();
    soketKur();
    panoCek();
    armaCek();
    davetBildir();
    setInterval(function () {
        panoCek();
        armaCek();
    }, 8000);
})();
