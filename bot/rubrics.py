"""Рубрики заметок, их знаки в Telegram (Unicode) и на обложках (SVG)."""

# Ключ -> (название в первой строке поста, знак Unicode)
RUBRICS = {
    "russia": ("РОССИЯ", "⊖"),
    "regions": ("РЕГИОНЫ", "▦"),
    "budget": ("БЮДЖЕТ", "◩"),
    "property": ("СОБСТВЕННОСТЬ", "⌂"),
    "finance": ("ФИНАНСЫ", "⚭"),
    "economy": ("ЭКОНОМИКА", "◑"),
    "business": ("БИЗНЕС", "△"),
    "sanctions": ("САНКЦИИ", "⊘"),
    "labour": ("ТРУД", "◧"),
    "postsoviet": ("ПОСТСОВЕТСКИЕ СТРАНЫ", "◫"),
    "china": ("КИТАЙ", "◇"),
    "newmarkets": ("НОВЫЕ РЫНКИ", "◬"),
    "europe": ("ЕВРОПА", "▱"),
    "usa": ("США", "▭"),
    "world": ("МИР", "◎"),
    "abroad": ("РОССИЯНЕ ЗА РУБЕЖОМ", "⇥"),
    "tech": ("НАУКА И ТЕХНОЛОГИИ", "⌬"),
}

_S = 'fill="none" stroke="#141414" stroke-width="0.9"'

# Знаки для обложки (viewBox 0 0 48 48). Те же фигуры, что в наборе на холсте.
SVG = {
    "russia": f'<svg viewBox="0 0 48 48" {_S}><circle cx="24" cy="24" r="16"/><line x1="8" y1="24" x2="40" y2="24"/></svg>',
    "regions": f'<svg viewBox="0 0 48 48" {_S}><rect x="8" y="8" width="14" height="14" fill="#141414"/><rect x="26" y="8" width="14" height="14"/><rect x="8" y="26" width="14" height="14"/><rect x="26" y="26" width="14" height="14"/></svg>',
    "budget": f'<svg viewBox="0 0 48 48" {_S}><rect x="8" y="8" width="32" height="32"/><line x1="8" y1="40" x2="40" y2="8"/></svg>',
    "property": f'<svg viewBox="0 0 48 48" {_S}><rect x="6" y="6" width="36" height="36"/><circle cx="24" cy="20" r="6"/><path d="M20.5 25 L18 36 H30 L27.5 25"/></svg>',
    "finance": f'<svg viewBox="0 0 48 48" {_S}><circle cx="17" cy="24" r="11"/><circle cx="31" cy="24" r="11"/></svg>',
    "economy": f'<svg viewBox="0 0 48 48" {_S}><circle cx="24" cy="24" r="16"/><path d="M24 8 A16 16 0 0 1 24 40 Z" fill="#141414"/></svg>',
    "business": f'<svg viewBox="0 0 48 48" {_S}><path d="M24 8 L40 40 H8 Z"/></svg>',
    "sanctions": f'<svg viewBox="0 0 48 48" {_S}><circle cx="24" cy="24" r="16"/><line x1="12.7" y1="35.3" x2="35.3" y2="12.7"/></svg>',
    "labour": f'<svg viewBox="0 0 48 48" {_S}><rect x="8" y="8" width="32" height="32"/><rect x="8" y="8" width="16" height="32" fill="#141414"/></svg>',
    "postsoviet": f'<svg viewBox="0 0 48 48" {_S}><rect x="8" y="8" width="32" height="32"/><line x1="24" y1="8" x2="24" y2="40"/></svg>',
    "china": f'<svg viewBox="0 0 48 48" {_S}><path d="M24 6 L42 24 L24 42 L6 24 Z"/></svg>',
    "newmarkets": f'<svg viewBox="0 0 48 48" {_S}><path d="M24 8 L40 40 H8 Z"/><line x1="16" y1="24" x2="32" y2="24"/></svg>',
    "europe": f'<svg viewBox="0 0 48 48" {_S}><path d="M14 14 H42 L34 34 H6 Z"/></svg>',
    "usa": f'<svg viewBox="0 0 48 48" {_S}><rect x="6" y="14" width="36" height="20"/></svg>',
    "world": f'<svg viewBox="0 0 48 48" {_S}><circle cx="24" cy="24" r="16"/><ellipse cx="24" cy="24" rx="7" ry="16"/></svg>',
    "abroad": f'<svg viewBox="0 0 48 48" {_S}><circle cx="18" cy="24" r="11"/><line x1="29" y1="24" x2="43" y2="24"/><path d="M37 18 L43 24 L37 30"/></svg>',
    "tech": f'<svg viewBox="0 0 48 48" {_S}><path d="M24 6 L40 15 V33 L24 42 L8 33 V15 Z"/><circle cx="24" cy="24" r="6"/></svg>',
}

FORMS = [
    "невозможный факт",
    "сухой тренд",
    "конец явления",
    "портрет",
    "вопрос",
    "итог срока",
    "чему учит",
    "странная мелочь",
    "абсурд как явление",
    "неожиданная география",
]

OPTICS = [
    "лестница власти", "пузырь веры", "козёл отпущения", "разумные по одному",
    "спектакль", "метрика-оборотень", "теневой двойник", "слепое пятно",
    "обменник", "жертвенная бухгалтерия", "обратный эффект",
    "сдвиг горизонта", "предел физики",
]
