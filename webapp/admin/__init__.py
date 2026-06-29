"""Admin web panel — alohida API + sahifalar.

Autentifikatsiya: foydalanuvchi Mini App'i bilan bir xil — Telegram WebApp
`initData` HMAC tekshiruvi (`webapp/auth.py:verify_init_data`, admin bot tokeni
bilan), ustiga rol whitelisti (`webapp/admin/auth.py:role_of` —
ADMIN_TELEGRAM_IDS / OPERATOR_TELEGRAM_IDS). Hech qanday JWT/cookie/magic-link
ishlatilmaydi.
"""
