import sys
import requests
import sqlite3
import winsound
from PyQt5.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, 
                             QLabel, QPushButton, QLineEdit, QListWidget, 
                             QMessageBox, QTableWidget, QTableWidgetItem, QHeaderView)
from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtGui import QFont

SUNUCU_URL = "http://127.0.0.1:5000"

class MezatAdminPanel(QWidget):
    def __init__(self):
        super().__init__()
        self.initUI()
        self.kalan_sure = 15
        self.timer = QTimer()
        self.timer.timeout.connect(self.sayac_guncelle)
        
        self.api_timer = QTimer()
        self.api_timer.timeout.connect(self.web_durum_senkronize)
        self.api_timer.start(1000)
        
        self.son_fiyat = 0.0

    def initUI(self):
        self.setWindowTitle("🏛️ Hamdullah Abi - Canlı Mezat Admin Paneli")
        self.setGeometry(100, 100, 900, 600)
        self.setStyleSheet("background-color: #1e1e2e; color: #ffffff;")

        main_layout = QHBoxLayout()
        left_layout = QVBoxLayout()

        self.lbl_urun = QLabel("📦 Ürün: Osmanlı Pirinç Şamdan")
        self.lbl_urun.setFont(QFont("Arial", 16, QFont.Bold))
        self.lbl_urun.setStyleSheet("color: #f9e2af;")
        left_layout.addWidget(self.lbl_urun)

        self.lbl_fiyat = QLabel("500.00 ₺")
        self.lbl_fiyat.setFont(QFont("Arial", 36, QFont.Bold))
        self.lbl_fiyat.setStyleSheet("color: #a6e3a1; margin: 10px 0;")
        left_layout.addWidget(self.lbl_fiyat)

        self.lbl_son_veren = QLabel("Son Pey Veren: -")
        self.lbl_son_veren.setFont(QFont("Arial", 14))
        left_layout.addWidget(self.lbl_son_veren)

        self.lbl_sayac = QLabel("⏳ Kalan Süre: 15 sn")
        self.lbl_sayac.setFont(QFont("Arial", 20, QFont.Bold))
        self.lbl_sayac.setStyleSheet("color: #f38ba8;")
        left_layout.addWidget(self.lbl_sayac)

        left_layout.addWidget(QLabel("👤 Müşteri / WhatsApp Pey Girişi:"))
        self.txt_musteri = QLineEdit()
        self.txt_musteri.setPlaceholderText("Müşteri Adı / Rumuz")
        self.txt_musteri.setStyleSheet("padding: 8px; font-size: 14px; background: #313244; color: white;")
        left_layout.addWidget(self.txt_musteri)

        btn_pey_ekle = QPushButton("⚡ Manuel Pey Ekle (+50 ₺)")
        btn_pey_ekle.setStyleSheet("background: #fab387; color: black; font-weight: bold; padding: 12px; font-size: 16px;")
        btn_pey_ekle.clicked.connect(self.manuel_pey_ver)
        left_layout.addWidget(btn_pey_ekle)

        main_layout.addLayout(left_layout, stretch=2)

        right_layout = QVBoxLayout()
        right_layout.addWidget(QLabel("📜 Canlı Pey Akışı:"))
        self.lst_peyler = QListWidget()
        self.lst_peyler.setStyleSheet("background: #313244; color: #cdd6f4; font-size: 13px;")
        right_layout.addWidget(self.lst_peyler)

        main_layout.addLayout(right_layout, stretch=1)
        self.setLayout(main_layout)

    def sayac_guncelle(self):
        if self.kalan_sure > 0:
            self.kalan_sure -= 1
            self.lbl_sayac.setText(f"⏳ Kalan Süre: {self.kalan_sure} sn")
        else:
            self.timer.stop()
            self.lbl_sayac.setText("🔨 SATILDI!")

    def web_durum_senkronize(self):
        try:
            res = requests.get(f"{SUNUCU_URL}/api/durum", timeout=1)
            if res.status_code == 200:
                data = res.json()
                yeni_fiyat = data.get("fiyat", 0)
                if yeni_fiyat > self.son_fiyat:
                    self.son_fiyat = yeni_fiyat
                    self.lbl_fiyat.setText(f"{yeni_fiyat:.2f} ₺")
                    self.lbl_son_veren.setText(f"Son Pey Veren: {data.get('son_pey_veren')}")
                    self.kalan_sure = 15
                    self.timer.start(1000)
        except:
            pass

    def manuel_pey_ver(self):
        musteri = self.txt_musteri.text().strip() or "Admin Pey"
        try:
            requests.post(f"{SUNUCU_URL}/api/pey-ver", json={"isim": musteri, "artis": 50})
            self.txt_musteri.clear()
        except:
            QMessageBox.warning(self, "Hata", "Web sunucusuna bağlanılamadı!")

if __name__ == '__main__':
    app = QApplication(sys.argv)
    ex = MezatAdminPanel()
    ex.show()
    sys.exit(app.exec_())