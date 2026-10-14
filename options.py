"""The choices shown to the user. Single source of truth for the page and the API."""

MOODS = [
    {"id": "happy", "label": "Happy", "emoji": "😄"},
    {"id": "sad", "label": "Sad", "emoji": "😢"},
    {"id": "calm", "label": "Calm", "emoji": "😌"},
    {"id": "energetic", "label": "Energetic", "emoji": "⚡"},
    {"id": "focused", "label": "Focused", "emoji": "🎯"},
    {"id": "romantic", "label": "Romantic", "emoji": "💞"},
    {"id": "nostalgic", "label": "Nostalgic", "emoji": "📼"},
]

TIMES = [
    {"id": "morning", "label": "Morning", "emoji": "🌅"},
    {"id": "afternoon", "label": "Afternoon", "emoji": "☀️"},
    {"id": "evening", "label": "Evening", "emoji": "🌇"},
    {"id": "night", "label": "Night", "emoji": "🌙"},
]

WEATHERS = [
    {"id": "sunny", "label": "Sunny", "emoji": "☀️"},
    {"id": "cloudy", "label": "Cloudy", "emoji": "☁️"},
    {"id": "rainy", "label": "Rainy", "emoji": "🌧️"},
    {"id": "snowy", "label": "Snowy", "emoji": "❄️"},
]

GENRES = [
    {"id": "any", "label": "Any", "emoji": "🎲"},
    {"id": "pop", "label": "Pop", "emoji": "🎤"},
    {"id": "rock", "label": "Rock", "emoji": "🎸"},
    {"id": "hiphop", "label": "Hip-hop", "emoji": "🎧"},
    {"id": "rnb", "label": "R&B & Soul", "emoji": "🎷"},
    {"id": "electronic", "label": "Electronic", "emoji": "🎛️"},
    {"id": "indie", "label": "Indie & Alt", "emoji": "🌿"},
    {"id": "folk", "label": "Acoustic & Reggae", "emoji": "🪕"},
    {"id": "jazz", "label": "Jazz & Classical", "emoji": "🎹"},
    {"id": "latin", "label": "Latin", "emoji": "💃"},
]


def ids(items):
    return [i["id"] for i in items]


def label(items, item_id):
    for i in items:
        if i["id"] == item_id:
            return i["label"]
    return item_id


def as_dict():
    return {"moods": MOODS, "times": TIMES, "weathers": WEATHERS, "genres": GENRES}
