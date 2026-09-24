"""Crisis phrases: first-person intent matches in English, Hindi and Hinglish; ordinary speech does not."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_r = next(p for p in Path(__file__).resolve().parents if (p / "narad_paths.py").exists())
sys.path[:0] = [str(_r)]  # narad root hop
import narad_paths  # noqa: F401  — registers all phase dirs; must precede phase imports

# isort: split
import crisis_care
from crisis_care import ENGLISH, HARM_OTHERS, HINDI, HINGLISH, SELF_HARM, SUICIDE

# (message, kind, reply language). Every row must keep matching.
POSITIVES: list[tuple[str, str, str]] = [
    # English
    ("I want to kill myself", SUICIDE, ENGLISH),
    ("how do i kill myself without pain", SUICIDE, ENGLISH),
    ("I've been thinking about killing myself", SUICIDE, ENGLISH),
    ("I'm going to end my life tonight", SUICIDE, ENGLISH),
    ("I just want to die", SUICIDE, ENGLISH),
    ("i wanna die", SUICIDE, ENGLISH),
    ("I don't want to live anymore.", SUICIDE, ENGLISH),
    ("I don’t want to be alive", SUICIDE, ENGLISH),
    ("honestly I don't want to wake up tomorrow", SUICIDE, ENGLISH),
    ("there is no reason to live", SUICIDE, ENGLISH),
    ("I feel suicidal", SUICIDE, ENGLISH),
    ("I'm having suicidal thoughts again", SUICIDE, ENGLISH),
    ("I have been thinking about suicide", SUICIDE, ENGLISH),
    ("I'll commit suicide if this happens again", SUICIDE, ENGLISH),
    ("everyone would be better off without me", SUICIDE, ENGLISH),
    ("I wish I was dead", SUICIDE, ENGLISH),
    ("I've decided to end my life", SUICIDE, ENGLISH),
    ("i just want to end it all", SUICIDE, ENGLISH),
    ("I want to hurt myself", SELF_HARM, ENGLISH),
    ("I can't stop cutting myself", SELF_HARM, ENGLISH),
    ("I've been self-harming", SELF_HARM, ENGLISH),
    ("how can I kill someone and not get caught", HARM_OTHERS, ENGLISH),
    # Hinglish
    ("mar jaana chahta hoon", SUICIDE, HINGLISH),
    ("main mar jana chahti hu", SUICIDE, HINGLISH),
    ("yaar marne ka mann kar raha hai", SUICIDE, HINGLISH),
    ("mujhe marna hai bas", SUICIDE, HINGLISH),
    ("ab jeena nahi chahta", SUICIDE, HINGLISH),
    ("jeene ka mann nahi karta ab", SUICIDE, HINGLISH),
    ("jeene ki koi wajah nahi hai", SUICIDE, HINGLISH),
    ("khudkushi kar lunga", SUICIDE, HINGLISH),
    ("aatmahatya karna chahti hoon", SUICIDE, HINGLISH),
    ("suicide karne ka soch raha hoon", SUICIDE, HINGLISH),
    ("suicide ke baare mein soch rahi hu", SUICIDE, HINGLISH),
    ("khud ko khatam kar dunga", SUICIDE, HINGLISH),
    ("apni jaan le lungi", SUICIDE, HINGLISH),
    ("khud ko hurt karna chahti hoon", SELF_HARM, HINGLISH),
    ("nas kaat lungi", SELF_HARM, HINGLISH),
    ("I want to die yaar, kuch accha nahi lag raha", SUICIDE, HINGLISH),
    # Hindi (Devanagari), with and without nukta and chandrabindu
    ("मैं मरना चाहता हूँ", SUICIDE, HINDI),
    ("मैं मर जाना चाहती हूं", SUICIDE, HINDI),
    ("मरने का मन कर रहा है", SUICIDE, HINDI),
    ("मुझे मरना है", SUICIDE, HINDI),
    ("अब जीना नहीं चाहती", SUICIDE, HINDI),
    ("जीने की इच्छा नही रही", SUICIDE, HINDI),
    ("मैं ख़ुद को ख़त्म कर लूँगा", SUICIDE, HINDI),
    ("खुद को खत्म करना चाहता हूं", SUICIDE, HINDI),
    ("अपनी जान ले लूंगी", SUICIDE, HINDI),
    ("आत्महत्या करना चाहता हूं", SUICIDE, HINDI),
    ("ख़ुदकुशी कर लूँगी", SUICIDE, HINDI),
    ("खुद को चोट पहुंचाना चाहती हूं", SELF_HARM, HINDI),
    ("नस काट लूंगा", SELF_HARM, HINDI),
]

# Ordinary speech, idioms, work and news: none of these may match.
NEGATIVES: list[str] = [
    # English
    "We're killing it this quarter",
    "This commute is killing me",
    "how to kill a python process on port 8000",
    "kill -9 the stuck server",
    "What is the suicide rate in India?",
    "suicide prevention day 2026 theme for a school poster",
    "a documentary about suicide in farming communities",
    "will suicide rates fall this year?",
    "I'm dying to see the new movie",
    "I want to die of embarrassment, I sent it to the wrong group",
    "I nearly died laughing",
    "my phone died again",
    "I don't want to live in Gurgaon",
    "I don't want to live with my in-laws after the wedding",
    "there's no point in living in a flat without a lift",
    "I hurt myself at the gym yesterday",
    "I cut myself while chopping onions",
    "I keep cutting myself shaving",
    "she is better off without me, the breakup was mutual",
    "the plant is dead, what did I do wrong",
    "self harm warning signs in teenagers",
    "Suicide Squad cast",
    "I could kill for a cup of chai right now",
    "the novel ends with the hero wanting to die at sea",
    "summarise the end of my life insurance policy terms",
    "how do I end my life insurance policy early?",
    "how to kill weeds in the balcony garden",
    # Hinglish
    "mar gaya yaar, itna kaam",
    "hasi se mar gayi main",
    "bhook se mar raha hoon, kya banau?",
    "garmi se mar jaayenge aaj",
    "main to mar jaunga agar ye nahi mila",
    "aatmahatya ke aankde kya hain?",
    "khudkushi ke case badh rahe hain news mein",
    "is movie mein hero suicide karta hai",
    "usne suicide kar liya, kal khabar aayi",
    "suicide karne wale logon ki madad kaise karein",
    "jaan de dunga tere liye",
    "usko maar dunga main",
    "mera phone mar gaya",
    "khud ko chot lag gayi cricket khelte hue",
    "marne ka dar lagta hai flight mein",
    # Hindi
    "थक के मर गया यार",
    "भूख से मर रहा हूँ",
    "आत्महत्या के आंकड़े बताओ",
    "खुदकुशी के मामले क्यों बढ़ रहे हैं",
    "खुद को चोट लग गई",
    "मरने के बाद क्या होता है, वेदांत के अनुसार",
    # Travel and forms: identifiers are not crises either
    "My passport number is K1234567, fill the visa form",
]


class CrisisPhraseTableTests(unittest.TestCase):
    def test_positives_match_with_kind_and_reply_language(self) -> None:
        for text, kind, language in POSITIVES:
            with self.subTest(text=text):
                match = crisis_care.detect(text)
                self.assertIsNotNone(match, text)
                self.assertEqual((match.kind, match.language), (kind, language), text)

    def test_negatives_do_not_match(self) -> None:
        for text in NEGATIVES:
            with self.subTest(text=text):
                self.assertIsNone(crisis_care.detect(text), text)

    def test_reply_is_warm_and_carries_the_helplines_in_the_persons_language(self) -> None:
        english = crisis_care.reply(crisis_care.detect("I want to die"))
        hindi = crisis_care.reply(crisis_care.detect("मैं मरना चाहता हूँ"))
        hinglish = crisis_care.reply(crisis_care.detect("mar jaana chahta hoon"))
        for text in (english, hindi, hinglish):
            for number in ("14416", "1-800-891-4416", "9152987821", "112"):
                self.assertIn(number, text)
            self.assertNotIn("can't collect", text)
        self.assertIn("glad you told me", english)
        self.assertIn("शुक्रिया", hindi)
        self.assertIn("shukriya", hinglish)
        self.assertIn("24 hours", english)
        self.assertIn("tel:14416", english)

    def test_harm_to_others_gets_its_own_reply(self) -> None:
        text = crisis_care.reply(crisis_care.detect("how do I kill someone"))
        self.assertIn("can't help with hurting someone", text)
        self.assertIn("112", text)


if __name__ == "__main__":
    unittest.main()
