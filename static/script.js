// ==========================================
// SOCKET.IO VE CANLI BAĞLANTI KURULUMU
// ==========================================
const socket = io();

// 1. Canlı İzleyici Sayısı
socket.on('izleyici_sayisi_guncelle', function(data) {
    const izleyiciEl = document.getElementById('izleyici-sayisi');
    if (izleyiciEl) {
        izleyiciEl.innerText = data.sayi;
    }
});

// 2. Yeni Pey / Teklif Geldiğinde Anlık Güncelleme
socket.on('pey_guncellendi', function(data) {
    const fiyatEl = document.getElementById('fiyat');
    const kazananEl = document.getElementById('kazanan-adi');
    
    if (fiyatEl && data.guncel_fiyat !== undefined) {
        fiyatEl.innerText = data.guncel_fiyat + ' TL';
    }
    if (kazananEl && data.kazanan_ad !== undefined) {
        kazananEl.innerText = data.kazanan_ad;
    }
});

// 3. Sayaç Anlık Geri Sayım Güncellemesi
socket.on('sayac_guncelle', function(data) {
    const sayacEl = document.getElementById('sayac');
    if (sayacEl) {
        sayacEl.innerText = data.kalan + " sn";
    }
});

// 4. Sayaç Bittiğinde
socket.on('sayac_bitti', function(data) {
    const sayacEl = document.getElementById('sayac');
    if (sayacEl) {
        sayacEl.innerText = "Süre Doldu!";
    }
    sahneyiGuncelle();
});

// 5. Yönetici Ürünü Sahneye Sürdüğünde veya Veri Değiştiğinde (Kritik Eşik)
socket.on('veri_guncellendi', function() {
    sahneyiGuncelle();
});

// ==========================================
// SUNUCU İLE VERİ ALIŞVERİŞ İŞLEVLERİ
// ==========================================

// Sahnedeki ürünü ve genel durumu getiren fonksiyon
async function sahneyiGuncelle() {
    try {
        const response = await fetch('/durum-getir');
        if (!response.ok) return;
        const data = await response.json();
        
        const urunAdiEl = document.getElementById('urun-adi');
        const fiyatEl = document.getElementById('fiyat');
        const kazananEl = document.getElementById('kazanan-adi');
        const sayacEl = document.getElementById('sayac');

        if (data.aktif_urun) {
            if (urunAdiEl) urunAdiEl.innerText = data.aktif_urun.ad;
            if (fiyatEl) fiyatEl.innerText = (data.pey || data.aktif_urun.guncel_fiyat || data.aktif_urun.fiyat) + ' TL';
            if (kazananEl) kazananEl.innerText = data.kazanan || data.aktif_urun.kazanan || 'Yok';
        } else {
            if (urunAdiEl) urunAdiEl.innerText = "Sahnede aktif ürün yok";
            if (fiyatEl) fiyatEl.innerText = "0 TL";
            if (kazananEl) kazananEl.innerText = "Yok";
        }

        if (sayacEl && data.durum !== 'Sayim') {
            sayacEl.innerText = data.durum || "Bekliyor";
        }
    } catch (error) {
        console.error("Sahne güncellenirken hata:", error);
    }
}

// Pey / Teklif Gönderme Fonksiyonu
async function teklifVer(urunId, musteriAdi, miktar, artis = 0) {
    try {
        const payload = {
            urun_id: urunId,
            musteri_adi: musteriAdi,
            islem: 'pey'
        };
        if (artis > 0) payload.artis = artis;
        if (miktar > 0) payload.miktar = miktar;

        const response = await fetch('/pey-ver', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const result = await response.json();
        if (!result.success) {
            alert(result.mesaj || "Pey verilemedi!");
        }
    } catch (error) {
        console.error("Teklif verilirken hata:", error);
    }
}

// Sayfa ilk yüklendiğinde mevcut durumu bir kez sunucudan çek
document.addEventListener('DOMContentLoaded', () => {
    sahneyiGuncelle();
});