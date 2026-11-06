const TikTok = require('tiktok-live-connector');
const http = require('http');

const tiktokUsername = 'halicantikmezat34';
const WebcastConnection = TikTok.WebcastPushConnection || TikTok.TikTokLiveConnection || TikTok.default || TikTok;

let tiktokLive = null;
let isReconnecting = false;

function postIstegiGonder(port, path, payload) {
    try {
        const postData = Buffer.from(JSON.stringify(payload), 'utf-8');

        const req = http.request({
            hostname: '127.0.0.1',
            port: port,
            path: path,
            method: 'POST',
            headers: {
                'Content-Type': 'application/json; charset=utf-8',
                'Content-Length': postData.length
            },
            timeout: 1500
        }, (res) => {
            res.on('data', () => {});
            res.on('end', () => {});
        });

        req.on('timeout', () => { req.destroy(); });
        req.on('error', () => {});

        req.write(postData);
        req.end();
    } catch (err) {}
}

const gecersizKelimeler = [
    'katıldı', 'begendi', 'beğendi', 'paylasti', 'paylaştı', 
    'followed', 'shared', 'joined', 'tiktok', 'hosgeldin', 'hoş geldin'
];

function flaskPeyGonder(user, text) {
    if (!text) return;
    const temizYazi = String(text).trim();
    const temizKullanici = String(user).trim();
    const kucuk = temizYazi.toLowerCase();

    if (temizYazi.length < 2 || temizYazi.length > 90) return;
    if (gecersizKelimeler.some(kelime => kucuk.includes(kelime))) return;

    console.log(`💬 [TikTok] ${temizKullanici}: "${temizYazi}"`);

    // 1. Hemen Al Kontrolü (Port 5000)
    if (kucuk.includes('hemen al') || kucuk.includes('aldim') || kucuk.includes('aldım')) {
        postIstegiGonder(5000, '/api/hemen-al-tetikle', {
            platform: 'TikTok',
            username: temizKullanici
        });
        return;
    }

    // 2. Web Canlı Akışı & Kayan Bant (Port 5000)
    postIstegiGonder(5000, '/api/tiktok-web-chat', {
        platform: 'TikTok',
        username: temizKullanici,
        message: temizYazi
    });

    // 3. Ahmet Usta AI & Pey Çözücü Motoru (Port 5005)
    postIstegiGonder(5005, '/canli_yorum', {
        platform: 'TikTok',
        user: temizKullanici,
        comment: temizYazi
    });
}

function baglan() {
    isReconnecting = false;

    if (tiktokLive) {
        try {
            tiktokLive.removeAllListeners();
            tiktokLive.disconnect();
        } catch (e) {}
        tiktokLive = null;
    }

    tiktokLive = new WebcastConnection(tiktokUsername, {
        processInitialData: false,
        enableExtendedGiftInfo: false,
        enableWebsocketUpgrade: true,
        requestPollingIntervalMs: 2000,
        clientParams: {
            app_language: 'tr-TR',
            device_platform: 'web'
        },
        requestOptions: { timeout: 10000 },
        websocketOptions: { timeout: 10000 }
    });

    console.log(`📡 @${tiktokUsername} TikTok bağlantısı başlatılıyor...`);

    tiktokLive.connect().then(state => {
        console.log(`🚀 TikTok Bağlandı! (Oda ID: ${state.roomId})`);
        console.log(`👂 Canlı sohbet dinleniyor...`);
    }).catch(err => {
        console.error(`❌ Bağlantı başarısız, 5 sn sonra tekrar denenecek:`, err.message || err);
        yenidenBaglan(5000);
    });

    tiktokLive.on('chat', data => {
        const user = data.uniqueId || data.nickname || 'Misafir';
        const msg = data.comment || data.text || '';
        if (msg) flaskPeyGonder(user, msg);
    });

    tiktokLive.on('disconnected', () => {
        console.log('⚠️ TikTok bağlantısı koptu. 3 sn içinde yeniden bağlanılıyor...');
        yenidenBaglan(3000);
    });

    tiktokLive.on('error', err => {
        console.error('⚠️ TikTok Akış Hatası:', err.message || err);
    });
}

function yenidenBaglan(beklemeSuresiMs) {
    if (isReconnecting) return;
    isReconnecting = true;
    setTimeout(() => { baglan(); }, beklemeSuresiMs);
}

baglan();