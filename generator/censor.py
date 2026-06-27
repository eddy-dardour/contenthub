#!/usr/bin/env python3
"""TikTok-safe censorship: replaces profanity AND sensitive words in-place.
No trigger warnings, no rejections — just clean replacements that keep the story intact."""

import re

REPLACEMENTS = {
    # f-word
    'fuck': 'freak', 'fucks': 'freaks', 'fucked': 'messed up',
    'fucking': 'freaking', 'fucker': 'jerk', 'motherfucker': 'jerk',
    'mofo': 'jerk', 'wtf': 'what the heck', 'stfu': 'be quiet',
    'fml': 'this stinks',
    # s-word
    'shit': 'crap', 'shits': 'crap', 'shitty': 'lousy',
    'bullshit': 'nonsense', 'bs': 'nonsense',
    # b-word
    'bitch': 'jerk', 'bitches': 'jerks', 'bitchy': 'rude',
    'bastard': 'jerk', 'bastards': 'jerks',
    # a-word
    'ass': 'butt', 'asses': 'butts', 'asshole': 'jerk', 'assholes': 'jerks',
    'jackass': 'jerk', 'dumbass': 'dummy', 'badass': 'tough', 'asshat': 'jerk',
    'smartass': 'wiseguy', 'kiss my ass': 'get lost',
    # d-word
    'damn': 'darn', 'damned': 'darned', 'goddamn': 'gosh darn',
    'goddamned': 'gosh darn', 'dick': 'jerk', 'dickhead': 'jerk',
    'douche': 'jerk', 'douchebag': 'jerk',
    # other profanity
    'crap': 'junk', 'piss': 'tick', 'pissed': 'annoyed', 'pissing': 'annoying',
    'cunt': 'jerk', 'twat': 'jerk', 'prick': 'jerk',
    'slut': 'person', 'whore': 'person', 'hoe': 'person',
    'retard': 'fool', 'retarded': 'silly', 'idiot': 'silly', 'moron': 'silly',
    'hell': 'heck', 'pussy': 'wimp', 'sucks': 'stinks', 'suck': 'stink',
    'effing': 'freaking', 'frigging': 'freaking', 'friggin': 'freaking',
    'crappy': 'lousy', 'scumbag': 'jerk', 'sleazebag': 'jerk',
    'perv': 'creep', 'pervert': 'creep', 'pos': 'jerk', 'sob': 'jerk',
    'screw you': 'get lost', 'go to hell': 'get lost',
    'shut up': 'be quiet', 'shut the hell up': 'be quiet',
    'loser': 'failure', 'pathetic': 'pitiful', 'disgusting': 'revolting',
    'cretin': 'fool', 'imbecile': 'fool', 'dimwit': 'fool', 'numskull': 'fool',
    'jerkoff': 'jerk', 'screw': 'mess',
    # ── Sensitive / TikTok-restricted terms ─────────────────────────────────
    # Violence
    'murder': 'wrongdoing', 'murdered': 'harmed', 'murderer': 'bad person',
    'kill': 'hurt', 'kills': 'hurts', 'killed': 'hurt', 'killing': 'hurting',
    'killer': 'bad person', 'massacre': 'tragedy', 'slaughter': 'tragedy',
    'slaughtered': 'harmed', 'bloodbath': 'chaos', 'gore': 'graphic content',
    'decapitate': 'injure', 'decapitated': 'injured', 'dismember': 'injure',
    'dismembered': 'injured', 'mutilate': 'injure', 'mutilated': 'injured',
    'torture': 'mistreat', 'tortured': 'mistreated', 'beheaded': 'harmed',
    'stabbed': 'attacked', 'stabbing': 'attack', 'gunshot': 'shot',
    'shot dead': 'harmed', 'shot him': 'attacked him', 'shot her': 'attacked her',
    'mass shooting': 'tragedy', 'school shooting': 'tragedy',
    'shoot up': 'attack', 'active shooter': 'attacker',
    'pipe bomb': 'device', 'homemade bomb': 'device',
    'dead': 'gone', 'die': 'leave', 'dies': 'leaves', 'died': 'passed away',
    'death': 'passing', 'deaths': 'losses',
    # Self-harm / suicide (replace without alarming)
    'suicide': 'crisis', 'suicidal': 'in crisis',
    'kill myself': 'give up', 'killing myself': 'giving up',
    'kill himself': 'give up', 'kill herself': 'give up',
    'self-harm': 'hurting oneself', 'self harm': 'hurting oneself',
    'selfharm': 'hurting oneself', 'cutting myself': 'hurting myself',
    'slit my wrists': 'hurt myself', 'hang myself': 'give up',
    'overdose': 'accident', 'overdosed': 'had an accident',
    'end my life': 'give up', 'cut myself': 'hurt myself',
    'slit wrists': 'hurt myself', 'self mutilation': 'self injury',
    'self inflicted': 'self caused',
    # Drugs
    'heroin': 'substance', 'cocaine': 'substance', 'meth': 'substance',
    'methamphetamine': 'substance', 'fentanyl': 'substance',
    'overdosing': 'having an accident', 'crack cocaine': 'substance',
    'crystal meth': 'substance', 'drug dealer': 'bad person',
    'drug addict': 'struggling person', 'opioid': 'painkiller',
    'opioids': 'painkillers', 'ketamine': 'substance',
    # Hate / extremism
    'nazi': 'extremist', 'genocide': 'tragedy', 'terrorist': 'extremist',
    'terrorism': 'extremism', 'white supremacy': 'extremism',
    'white supremacist': 'extremist', 'ethnic cleansing': 'tragedy',
    'hate crime': 'attack', 'radicalized': 'misled', 'radicalizing': 'misleading',
    # Abuse
    'abuse': 'mistreat', 'abused': 'mistreated', 'abuser': 'bad person',
    'child abuse': 'mistreatment', 'minor abuse': 'mistreatment',
    'molest': 'mistreat', 'molested': 'mistreated', 'molestation': 'mistreatment',
    'pedophile': 'predator', 'pedophilia': 'predatory behavior',
    'human trafficking': 'exploitation', 'sex trafficking': 'exploitation',
    # Misc sensitive
    'toxic': 'harmful', 'manipulate': 'control', 'manipulated': 'controlled',
    'manipulator': 'controlling person', 'narcissist': 'selfish person',
    'psycho': 'unstable person', 'crazy': 'unhinged', 'insane': 'wild',
    'stupid': 'unwise', 'dumb': 'unwise', 'ugly': 'unkind',
    'hate': 'dislike', 'hates': 'dislikes', 'hated': 'disliked',
    'destroy': 'ruin', 'destroys': 'ruins', 'destroyed': 'ruined',
}

def _match_case(original: str, replacement: str) -> str:
    if original.isupper():
        return replacement.upper()
    if original[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement

_PATTERN = re.compile(
    r'\b(' + '|'.join(re.escape(w) for w in sorted(REPLACEMENTS, key=len, reverse=True)) + r')\b',
    re.IGNORECASE,
)

def censor(text: str) -> str:
    """Replace all profanity and sensitive words in-place, preserving story flow."""
    if not text:
        return text
    return _PATTERN.sub(lambda m: _match_case(m.group(0), REPLACEMENTS[m.group(0).lower()]), text)

