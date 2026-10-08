const TikTok = require('tiktok-live-connector');
const http = require('http');

const tiktokUsername = 'halicantikmezat34';
const WebcastConnection = TikTok.WebcastPushConnection || TikTok.TikTokLiveConnection || TikTok.default || TikTok;

let tiktokLive;

function flaskPeyGonder(user, text) {
    if (!text) return;
    console.log(`💬 [YAKALANDI] ${user}: "${text}"`);

    const postData = JSON.stringify({
        username: String(user).trim(),
        message: String(text).trim()
    });

    const req = http.request({
        hostname: '127.0.0.1',
        port: 5000,
        path: '/api/tiktok-web-chat',
        method: 'POST',
        headers: {
            'Content-Type': 'application/json; charset=utf-8',
            'Content-Length': Buffer.byteLength(postData)
        },
        timeout: 4000
    }, (res) => {
        // Flask sunucusunun kilitlenmesini engelleyen okuma:
        res.on('data', () => {});
        res.on('end', () => {});
    });

    req.on('error', (e) => console.error(`❌ Flask Hatası: ${e.message}`));
    req.write(postData);
    req.end();
}

function baglan() {
    tiktokLive = new WebcastConnection(tiktokUsername, {
        processInitialData: true,
        enableExtendedGiftInfo: true,
        enableWebsocketUpgrade: true, // WebSocket ile anlık ve kesintisiz sohbet akışı
        requestPollingIntervalMs: 1000
    });

    console.log(`📡 @${tiktokUsername} canlı yayınına bağlanılıyor...`);

    tiktokLive.connect().then(state => {
        console.log(`\n==============================================`);
        console.log(`🚀 TikTok Bağlandı! (Oda: ${state.roomId})`);
        console.log(`👂 Chat dinleniyor...`);
        console.log(`==============================================\n`);
    }).catch(err => {
        console.error(`❌ Bağlantı kurulamadı, 5 sn sonra tekrar denenecek:`, err.message || err);
        setTimeout(baglan, 5000);
    });

    tiktokLive.on('chat', data => {
        const user = data.uniqueId || data.nickname || 'Misafir';
        const msg = data.comment || data.text || '';
        if (msg) {
            flaskPeyGonder(user, msg);
        }
    });

    tiktokLive.on('disconnected', () => {
        console.log('⚠️ Bağlantı kesildi. 3 saniye içinde yeniden bağlanılıyor...');
        setTimeout(baglan, 3000);
    });

    tiktokLive.on('error', err => {
        console.error('⚠️ Akış Uyarısı:', err.message || err);
    });
}

baglan();