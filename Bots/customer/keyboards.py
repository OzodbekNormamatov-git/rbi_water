from __future__ import annotations

from typing import Optional, Sequence

from aiogram.types import (
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    WebAppInfo,
)

from Domain.models.food import Food


# ---------------------- Tugmalar matnlari (markerlar) ----------------------
# Markerlar — bot.py da F.text == ... orqali tutiladi.

BTN_PRODUCTS = "💧 Mahsulotlar"
BTN_CART = "🛒 Savatcha"
BTN_MY_ORDERS = "📦 Mening buyurtmalarim"

BTN_BACK = "⬅️ Orqaga"
BTN_CHECKOUT = "📃 Buyurtmani rasmiylashtirish"
BTN_CLEAR_CART = "🗑 Savatchani tozalash"

BTN_OPEN_WEBAPP = "🌐 Tezkor buyurtma (Web App)"

BTN_CONFIRM = "✅ Tasdiqlash"
BTN_CANCEL = "❌ Bekor qilish"

BTN_SEND_PHONE = "📱 Raqamni yuborish"
BTN_SEND_LOCATION = "📍 Lokatsiyamni yuborish"


# ---------------------- Asosiy menyu ----------------------

def main_menu_kb(webapp_url: Optional[str] = None) -> ReplyKeyboardMarkup:
    """Mijoz asosiy menyusi.

    Eslatma: Mini App'ni ochish uchun chat input chap pastdagi **Menu Button**
    ishlatiladi (`main.py:setChatMenuButton`). Reply-keyboard'da takroriy
    "Web App" tugmasi qo'yilmaydi — bir xil funksiya 2 ta joyda noqulay.
    `webapp_url` parametri call-site'larni buzmaslik uchun saqlangan.
    """
    rows: list[list[KeyboardButton]] = [
        [KeyboardButton(text=BTN_PRODUCTS)],
        [KeyboardButton(text=BTN_CART), KeyboardButton(text=BTN_MY_ORDERS)],
    ]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


# ---------------------- Mahsulotlar menyusi ----------------------

def products_menu_kb(foods: Sequence[Food]) -> ReplyKeyboardMarkup:
    """Mahsulotlar reply-keyboardi: 2 ustunli grid + Orqaga + Savatcha.

    Mahsulot tugmasi matni — Food.name (bot.py da nom orqali topiladi).
    """
    rows: list[list[KeyboardButton]] = []
    buttons = [KeyboardButton(text=f.name) for f in foods]
    for i in range(0, len(buttons), 2):
        rows.append(buttons[i : i + 2])

    # "Orqaga" ni oxirgi qatorga juftlik bo'lib qo'shamiz, agar joy bo'lsa.
    back_btn = KeyboardButton(text=BTN_BACK)
    if rows and len(rows[-1]) == 1:
        rows[-1].append(back_btn)
    else:
        rows.append([back_btn])

    rows.append([KeyboardButton(text=BTN_CART)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


# ---------------------- Mahsulot ichidagi miqdor menyusi ----------------------

def quantity_kb() -> ReplyKeyboardMarkup:
    """Mahsulot kartasi ochilganda chiqadigan miqdor tugmalari: 3..11 + Orqaga."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="3"), KeyboardButton(text="4"), KeyboardButton(text="5")],
            [KeyboardButton(text="6"), KeyboardButton(text="7"), KeyboardButton(text="8")],
            [KeyboardButton(text="9"), KeyboardButton(text="10"), KeyboardButton(text="11")],
            [KeyboardButton(text=BTN_BACK)],
        ],
        resize_keyboard=True,
    )


# ---------------------- Savatcha menyusi ----------------------

def cart_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_CHECKOUT)],
            [KeyboardButton(text=BTN_CLEAR_CART), KeyboardButton(text=BTN_BACK)],
        ],
        resize_keyboard=True,
    )


# ---------------------- Tasdiqlash menyusi ----------------------

def confirm_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_CONFIRM)],
            [KeyboardButton(text=BTN_CANCEL)],
        ],
        resize_keyboard=True,
    )


# ---------------------- Telefon / Lokatsiya so'rash ----------------------

def request_phone_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_SEND_PHONE, request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def request_location_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_SEND_LOCATION, request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def remove_kb() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()
