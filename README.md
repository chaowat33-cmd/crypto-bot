# ⚡ Bitkub vs Binance Live Price Comparison & Arbitrage Dashboard

ระบบ Dashboard เปรียบเทียบราคาสดแบบเรียลไทม์ระหว่าง **Bitkub** และ **Binance** กว่า 250 คู่เหรียญ พร้อมคำนวณสเปรด (Spread) และค้นหาโอกาสทำ Arbitrage

## 🚀 ฟีเจอร์เด่น
- **Live Price Feed**: ดึงข้อมูลราคา Bid/Ask, Last Price, 24h High/Low, และ 24h Volume สดจาก Bitkub Public API และ Binance Public API
- **อัตราแลกเปลี่ยนแบบไดนามิก**: คำนวณอัตรา USDT/THB สดจาก Bitkub โดยตรงเพื่อแปลงราคา Binance เป็นบาท (THB) ได้อย่างแม่นยำ
- **Spread & Arbitrage Detection**: คำนวณส่วนต่างราคา (%) และวิเคราะห์กลยุทธ์:
  - 🟢 **Bitkub Premium**: ซื้อ Binance ➔ โอนขาย Bitkub (เมื่อราคา Bitkub สูงกว่า)
  - 🟡 **Bitkub Discount**: ซื้อ Bitkub ➔ โอนขาย Binance (เมื่อราคา Bitkub ต่ำกว่า)
- **Arbitrage Calculator**: มีเครื่องคิดเลขจำลองการซื้อขาย หักค่าธรรมเนียมจริง (Bitkub 0.25%, Binance 0.10%) คำนวณ Net Profit และ ROI สุทธิ
- **ตัวกรองและจัดเรียง**: กรองเหรียญหลัก (Top Major), เหรียญที่มีโอกาส Arbitrage (>0.5%), ค้นหาชื่อเหรียญ, และจัดเรียงตามสเปรดหรือวอลุ่ม
- **Zero Dependencies**: รันด้วย Python Standard Library เพียงคำสั่งเดียว ไม่ต้องติดตั้งแพ็กเกจภายนอก

## 💻 วิธีการเปิดใช้งาน
1. รันไฟล์ `start_dashboard.bat` หรือเปิด Terminal แล้วสั่ง:
   ```bash
   python server.py
   ```
2. เปิดเบราว์เซอร์ไปที่:
   ```
   http://localhost:5000
   ```
