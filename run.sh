#!/bin/bash
# 1. Ses modelini kontrol et, yoksa otomatik indir
python model_indir.py

# 2. Ahmet Usta bot servisini (5005 portu) arka planda 7/24 başlat
python canli_bot.py &

# 3. Ana mezat uygulamasını ve WebSocket motorunu başlat
python app.py