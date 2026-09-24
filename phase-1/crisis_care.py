"""crisis_care — suicidal intent and self-harm phrases in English, Hindi and Hinglish.

The Dharma input gate (server.py /chat) checks every message here before any
model sees it. A match never reaches a model, cloud or local: the person gets
an immediate, warm reply in the language they wrote in, with India's
helplines, and Karma notes that the gate answered (kind and language, never
the words).

Patterns need first-person intent ("I want to die", "mar jaana chahta hoon",
"जीना नहीं चाहती"). Exaggeration ("mar gaya yaar, itna kaam"), idioms
("killing it", "bhook se mar raha hoon") and questions about suicide in the
news ("aatmahatya ke aankde") do not match. phase-1/test_crisis_care.py holds
the positive and negative table; add a row there with every change here.

Helplines (checked 2026-09-24):
  Tele-MANAS 14416 / 1-800-891-4416 — Government of India (MoHFW), free, 24x7,
    20+ languages.
  iCall 9152987821 — TISS counsellors, Monday to Saturday (published clock
    hours differ between iCall's own pages, so none are stated).
  112 — national emergency number (ERSS).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

SUICIDE, SELF_HARM, HARM_OTHERS = "suicide", "self_harm", "harm_others"
ENGLISH, HINDI, HINGLISH = "en", "hi", "hinglish"

# Word edges for Latin and Devanagari text (\b does not see Devanagari
# vowel signs as word characters).
_S = r"(?<![a-z0-9ऀ-ॿ])"
_E = r"(?![a-z0-9ऀ-ॿ])"
# Devanagari negation, with or without the final anusvara (नहीं / नही).
_NAHI = "नहीं?"

# (kind, language, pattern) over normalised text: lowercase, no apostrophes,
# no nukta, chandrabindu folded to anusvara, single spaces.
_RULES: list[tuple[str, str, str]] = [
    # ── English: suicide ──────────────────────────────────────────────────
    (SUICIDE, ENGLISH, r"kill\s+myself"),
    (SUICIDE, ENGLISH,
     r"(?:want|wanna|going|gonna|planning|plan|thinking\s+(?:about|of)|tempted|decided|ready|tried|trying)"
     r"\s+(?:to\s+)?(?:killing|kill|hanging|hang|ending|end|poisoning|poison)\s+myself"),
    (SUICIDE, ENGLISH, r"how\s+(?:to|do\s+i|can\s+i|should\s+i)\s+(?:kill|hang|poison|end)\s+myself"),
    (SUICIDE, ENGLISH, r"end\s+my\s+(?:own\s+)?life"),
    (SUICIDE, ENGLISH, r"take\s+my\s+own\s+life"),
    (SUICIDE, ENGLISH, r"(?:want|wanna|going|gonna|ready)\s+to\s+(?:end\s+it\s+all|take\s+my\s+life)"),
    (SUICIDE, ENGLISH,
     r"i\s+(?:just\s+|really\s+|only\s+|kind\s+of\s+|kinda\s+)?(?:want|wanna)\s+(?:to\s+)?die"
     r"(?!\s+(?:of|from|laughing|with|for|in\s+your|in\s+his|in\s+her))"),
    (SUICIDE, ENGLISH, r"i\s+wish\s+i\s+(?:was|were|could\s+be)\s+dead"),
    (SUICIDE, ENGLISH, r"i\s+wish\s+i\s+(?:was|were|had)\s+never\s+(?:been\s+)?born"),
    # "I don't want to live" ends the clause; "... to live in Pune" does not match.
    (SUICIDE, ENGLISH,
     r"(?:dont|do\s+not|no\s+longer|never)\s+want\s+to\s+(?:be\s+alive|exist|live|wake\s+up)"
     r"(?:\s+any\s*more)?"
     r"(?=\s*(?:$|[.,!?;:]|(?:and|but|because|cause|coz|now|after|tomorrow|again|ever)\b))"),
    (SUICIDE, ENGLISH,
     r"no\s+(?:reason|point)\s+(?:to|in)\s+(?:live|living|being\s+alive)"
     r"(?!\s+(?:in|at|near|with|here|there|on|by|like)\b)"),
    (SUICIDE, ENGLISH,
     r"i(?:m|\s+am|\s+feel|\s+have\s+been|ve\s+been|\s+keep\s+feeling|\s+get|\s+was)\s+"
     r"(?:so\s+|really\s+|very\s+|kind\s+of\s+|kinda\s+|a\s+bit\s+|quite\s+)?suicidal"),
    (SUICIDE, ENGLISH, r"(?:i\s+have|i\s+get|im\s+having|i\s+am\s+having|having|getting)\s+suicidal\s+(?:thoughts|feelings|urges)"),
    (SUICIDE, ENGLISH,
     r"(?:want|wanna|going|gonna|planning|plan|thinking\s+(?:about|of)|about\s+to|decided|i\s+will|ill|i\s+might)"
     r"\s+(?:to\s+)?(?:commit(?:ting)?\s+)?suicide"
     r"(?!\s+(?:prevention|rates?|statistics|stats|awareness|helplines?|squad|notes?|cases?|numbers?|data|in\s+india)\b)"),
    (SUICIDE, ENGLISH, r"better\s+off\s+dead"),
    (SUICIDE, ENGLISH,
     r"(?:everyone|everybody|they|my\s+family|my\s+parents|the\s+world|people)\b[^.!?]{0,24}"
     r"better\s+off\s+without\s+me"),
    # ── English: self-harm ────────────────────────────────────────────────
    # Intent, not accidents: "I hurt myself at the gym" does not match.
    (SELF_HARM, ENGLISH,
     r"(?:want|wanna|going|gonna|urge|urges|need|thinking\s+(?:about|of)|started|been|stop)\s+"
     r"(?:to\s+)?(?:hurting|hurt|harming|harm|cutting|cut|burning|burn)\s+myself"
     r"(?!\s+(?:at|in|during|while|when|playing|lifting|on|with|by\s+accident|accidentally)\b)"),
    (SELF_HARM, ENGLISH,
     r"cutting\s+myself(?!\s+(?:shaving|while|when|on|in|by\s+accident|accidentally|off|some\s+slack)\b)"),
    (SELF_HARM, ENGLISH, r"i(?:ve|\s+have)?\s+(?:been\s+)?self[\s-]?harm(?:ing|ed)?"),
    # ── English: harm to someone else ─────────────────────────────────────
    (HARM_OTHERS, ENGLISH,
     r"how\s+(?:to|do\s+i|can\s+i)\s+(?:kill|seriously\s+harm|murder|poison)\s+(?:someone|somebody|a\s+person|people)"),

    # ── Hinglish (Roman Hindi): suicide ───────────────────────────────────
    (SUICIDE, HINGLISH,
     r"(?:mar\s*ja+n?a+|mar\s*jau+n?|mar\s*ja+un|mar\s*ja+ne|marna|marne)\s+(?:hi\s+)?(?:cha+h?ta|cha+h?ti|chahata|chahati|chahtha|chahthi)"),
    (SUICIDE, HINGLISH, r"marne\s+(?:ka|ki)\s+(?:mann|man|dil|irada|iraada|ichha|iccha|icchha|khayal|khyal)"),
    (SUICIDE, HINGLISH, r"(?:mujhe|mujhko|muje)\s+(?:bas\s+)?(?:marna|mar\s*ja+na)\s+hai"),
    (SUICIDE, HINGLISH,
     r"(?:jeena|jina|jeene|jine)\s+(?:hi\s+)?(?:nahi|nahin|nhi|nai)\s+(?:cha+h?ta|cha+h?ti|chahata|chahati|hai)"),
    (SUICIDE, HINGLISH,
     r"(?:jeene|jine)\s+(?:ka|ki|ke)\s+(?:mann|man|dil|ichha|iccha|icchha|irada|wajah|vajah|koi\s+wajah|matlab)\s+(?:hi\s+)?(?:nahi|nahin|nhi|nai)"),
    (SUICIDE, HINGLISH, r"(?:ab|aur)\s+(?:nahi|nahin|nhi)\s+(?:jeena|jina)"),
    (SUICIDE, HINGLISH,
     r"(?:khud\s*ko|apne\s*(?:aap|ap)\s*ko)\s+(?:khatam|khatm|khtm|maar|mar|maarna|marna|maar\s*dal)"),
    (SUICIDE, HINGLISH,
     r"apni\s+(?:jaan|jan|zindagi|zindgi|jindagi)\s+(?:le|lena|leni|le\s*lu|le\s*lunga|le\s*lungi|khatam|khatm|khtm)"),
    (SUICIDE, HINGLISH,
     r"(?:zindagi|zindgi|jindagi|jindgi)\s+(?:khatam|khatm|khtm)\s+(?:kar\s*(?:lu|lun|loon|lunga|lungi|dunga|dungi|du|doon)|karna|karne)"),
    (SUICIDE, HINGLISH,
     r"(?:khudkh?ushi|khudkushi|khudkhushi|aatmahatya|atmahatya|aatmhatya|atmhatya|suicide|sucide)\s+"
     r"(?:kar\s*(?:lu|lun|loo|loon|lunga|lungi|lenge|dunga|dungi)|karunga|karungi|karoon|karu|"
     r"karna\s+(?:cha+h?ta|cha+h?ti|chahata|chahati|hai)|karne\s+(?:ka|ki|wala|wali|ja\s+raha|ja\s+rahi|ja\s+rha|ja\s+rhi)|"
     r"(?:ke\s+)?(?:baare|bare)\s+(?:mein|me|mai)\s+soch\s+(?:raha|rahi|rha|rhi))"),
    # ── Hinglish: self-harm ───────────────────────────────────────────────
    # Intent, not accidents: "khud ko chot lag gayi" does not match.
    (SELF_HARM, HINGLISH,
     r"(?:khud\s*ko|apne\s*(?:aap|ap)\s*ko)\s+(?:nuksaan|nuksan|nuqsan|chot|hurt|harm)\s+"
     r"(?:pahunchana|pahuchana|pahunchane|pohonchana|karna|karne|kar\s*(?:lu|lun|lunga|lungi|dunga|dungi))"),
    (SELF_HARM, HINGLISH,
     r"(?:khud\s*ko|apne\s*(?:aap|ap)\s*ko)\s+(?:kaat|kat)\s*(?:lu|lun|lunga|lungi|leta|leti|raha|rahi|rha|rhi|dunga|dungi)"),
    (SELF_HARM, HINGLISH,
     r"(?:nas|nass|kalai)\s+(?:kaat|kat)\s*(?:lu|lun|loon|lunga|lungi|li|dunga|dungi|raha|rahi|rha|rhi|leta|leti)"),

    # ── Hindi (Devanagari): suicide ───────────────────────────────────────
    (SUICIDE, HINDI, r"(?:मरना|मर\s*जाना|मर\s*जाऊं|मर\s*जाने)\s+(?:ही\s+)?(?:चाहता|चाहती)"),
    (SUICIDE, HINDI, r"मरने\s+(?:का|की)\s+(?:मन|दिल|इरादा|इच्छा|ख्याल|खयाल)"),
    (SUICIDE, HINDI, r"(?:मुझे|मुझको)\s+(?:बस\s+)?(?:मरना|मर\s*जाना)\s+है"),
    (SUICIDE, HINDI, rf"(?:जीना|जीवित\s+रहना|जिंदा\s+रहना)\s+(?:ही\s+)?{_NAHI}\s+(?:चाहता|चाहती|है)"),
    (SUICIDE, HINDI, rf"जीने\s+(?:का|की|के)\s+(?:मन|दिल|इच्छा|इरादा|वजह|कोई\s+वजह|मतलब)\s+(?:ही\s+)?{_NAHI}"),
    (SUICIDE, HINDI, rf"(?:अब|और)\s+{_NAHI}\s+जीना"),
    (SUICIDE, HINDI, r"(?:खुद|अपने\s*आप)\s+को\s+(?:खत्म|खतम|मार(?:ना|ने)?)"),
    (SUICIDE, HINDI, r"अपनी\s+(?:जान|जिंदगी|जिन्दगी)\s+(?:ले(?:ना|ने)?|खत्म|खतम)"),
    (SUICIDE, HINDI,
     r"(?:जिंदगी|जिन्दगी)\s+(?:खत्म|खतम)\s+(?:कर\s*(?:लूं|लूंगा|लूंगी|लू|दूं|दूंगा|दूंगी)|करना|करने)"),
    (SUICIDE, HINDI,
     r"(?:आत्महत्या|खुदकुशी|खुदखुशी|सुसाइड)\s+"
     r"(?:कर\s*(?:लूं|लू|लूंगा|लूंगी|लेंगे|दूंगा|दूंगी)|करूंगा|करूंगी|करूं|"
     r"करना\s+(?:चाहता|चाहती|है)|करने\s+(?:का|की|वाला|वाली|जा\s+रहा|जा\s+रही)|"
     r"(?:के\s+)?बारे\s+में\s+सोच\s+(?:रहा|रही))"),
    # ── Hindi: self-harm ──────────────────────────────────────────────────
    # Intent, not accidents: "खुद को चोट लग गई" does not match.
    (SELF_HARM, HINDI,
     r"(?:खुद|अपने\s*आप)\s+को\s+(?:चोट|नुकसान|हर्ट)\s+"
     r"(?:पहुंचा(?:ना|ने|ती|ता|ऊं|ऊंगा|ऊंगी)?|कर(?:ना|ने|\s*लूं|\s*लूंगा|\s*लूंगी|\s*रहा|\s*रही))"),
    (SELF_HARM, HINDI, r"(?:नस|नसें|कलाई)\s+काट\s*(?:लूं|लूंगा|लूंगी|ली|दूंगा|दूंगी|रहा|रही)"),
]

_COMPILED = [(kind, language, re.compile(_S + pattern + _E)) for kind, language, pattern in _RULES]

# Common Roman-Hindi words: two or more in an English-pattern match means the
# person writes Hinglish, so the reply does too.
_HINGLISH_WORDS = re.compile(
    _S + r"(?:hai|hain|hoon|hu|nahi|nahin|nhi|main|mein|mujhe|mera|meri|yaar|kya|kuch|bahut|"
    r"bohot|ab|aur|koi|sab|kar|raha|rahi|lagta|lag|mann|dil|zindagi|bas|kyun|kaise)" + _E
)
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")


@dataclass(frozen=True)
class CrisisMatch:
    kind: str  # suicide | self_harm | harm_others
    language: str  # en | hi | hinglish: the language to answer in


def normalise(text: str) -> str:
    """Fold the spellings people type into one form the patterns expect."""
    folded = unicodedata.normalize("NFC", text or "")
    folded = folded.replace("़", "")  # nukta: ख़ुद → खुद (NFC keeps it separate)
    folded = folded.replace("ँ", "ं")  # chandrabindu → anusvara: हूँ → हूं
    folded = re.sub(r"[‘’'`]", "", folded.lower())  # don't → dont
    folded = re.sub(r"[​-‍]", "", folded)  # zero-width joiners from Indic keyboards
    return re.sub(r"\s+", " ", folded).strip()


def detect(text: str) -> CrisisMatch | None:
    """The first crisis rule the message matches, or None."""
    folded = normalise(text)
    if not folded:
        return None
    for kind, language, pattern in _COMPILED:
        if pattern.search(folded):
            return CrisisMatch(kind=kind, language=_reply_language(folded, language))
    return None


def _reply_language(folded: str, matched: str) -> str:
    if _DEVANAGARI.search(folded):
        return HINDI
    if matched == HINGLISH or len(_HINGLISH_WORDS.findall(folded)) >= 2:
        return HINGLISH
    return ENGLISH


_CARE = {
    ENGLISH: (
        "I'm really glad you told me. It sounds like you are carrying something very heavy "
        "right now, and you don't have to carry it alone.\n\n"
        "If you can, please reach out to someone you trust right now: someone in the family, "
        "a friend, anyone who can be with you or stay on the phone with you.\n\n"
        "You can also talk to a trained counsellor, free and in confidence:\n\n"
        "- **Tele-MANAS: [14416](tel:14416)** or [1-800-891-4416](tel:18008914416). "
        "Government of India, free, 24 hours a day, every day, in many Indian languages.\n"
        "- **iCall: [9152987821](tel:9152987821)**. Counsellors from TISS, Monday to Saturday.\n\n"
        "If you are in danger right now, or have already hurt yourself, call "
        "**[112](tel:112)** or go to the nearest hospital emergency.\n\n"
        "I'm here, and you can keep talking to me."
    ),
    HINDI: (
        "मुझे बताने के लिए शुक्रिया। लगता है आप इस समय बहुत भारी बोझ उठा रहे हैं, "
        "और आपको यह अकेले नहीं उठाना है।\n\n"
        "हो सके तो अभी किसी भरोसेमंद इंसान से बात कीजिए: घर का कोई सदस्य, कोई दोस्त, "
        "कोई भी जो आपके पास रह सके या फ़ोन पर आपके साथ बना रहे।\n\n"
        "आप किसी प्रशिक्षित काउंसलर से भी मुफ़्त और गोपनीय बात कर सकते हैं:\n\n"
        "- **टेली-मानस (Tele-MANAS): [14416](tel:14416)** या [1-800-891-4416](tel:18008914416)। "
        "भारत सरकार की सेवा, मुफ़्त, चौबीसों घंटे, हर दिन, कई भारतीय भाषाओं में।\n"
        "- **iCall: [9152987821](tel:9152987821)**। TISS के काउंसलर, सोमवार से शनिवार।\n\n"
        "अगर आप अभी ख़तरे में हैं, या ख़ुद को चोट पहुंचा चुके हैं, तो तुरंत "
        "**[112](tel:112)** पर कॉल कीजिए या सबसे पास के अस्पताल की इमरजेंसी में जाइए।\n\n"
        "मैं यहीं हूं, आप मुझसे बात करते रहिए।"
    ),
    HINGLISH: (
        "Mujhe batane ke liye shukriya. Lagta hai aap abhi bahut bhaari bojh utha rahe hain, "
        "aur aapko yeh akele nahi uthana hai.\n\n"
        "Ho sake to abhi kisi bharosemand insaan se baat kijiye: ghar ka koi, koi dost, "
        "koi bhi jo aapke paas reh sake ya phone par aapke saath bana rahe.\n\n"
        "Aap kisi trained counsellor se bhi free aur confidential baat kar sakte hain:\n\n"
        "- **Tele-MANAS: [14416](tel:14416)** ya [1-800-891-4416](tel:18008914416). "
        "Bharat sarkar ki seva, free, 24 ghante, har din, kai Indian bhashaon mein.\n"
        "- **iCall: [9152987821](tel:9152987821)**. TISS ke counsellors, Somvar se Shanivar.\n\n"
        "Agar aap abhi khatre mein hain, ya khud ko chot pahuncha chuke hain, to turant "
        "**[112](tel:112)** par call kijiye ya sabse paas ke hospital ki emergency mein jaiye.\n\n"
        "Main yahin hoon, aap mujhse baat karte rahiye."
    ),
}

_HARM_OTHERS = {
    ENGLISH: (
        "I can't help with hurting someone. If you are angry or overwhelmed enough to be "
        "thinking about it, please step away for a moment and talk to someone you trust.\n\n"
        "Tele-MANAS ([14416](tel:14416), free, 24 hours a day) is there for feelings like "
        "these too. If anyone is in danger right now, call **[112](tel:112)**."
    ),
}


def reply(match: CrisisMatch) -> str:
    """The message Narad sends at once, in the person's language."""
    texts = _HARM_OTHERS if match.kind == HARM_OTHERS else _CARE
    return texts.get(match.language) or texts[ENGLISH]
