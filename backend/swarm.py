"""
Swarm debate engine: N local LLM "members" share one chat stream, take a stance, argue,
trade points, form alliances, and only stop when every living member endorses one identical
statement (or the user force-stops). No GUI dependency — drive it with emit(kind, data).

Flow
  frame     a small judge model rewrites the user's message as ONE yes/no proposition P.
  round 0   every member answers alone with  stance (YES | NO | CONDITIONAL)  + a statement.
  round 1+  every member sees everyone's stance/statement, the PUBLIC standings (ratings and
            alliances) and replies with the same fields plus
              endorse       the member whose statement they accept word for word
              convinced_by  who convinced them, if they changed stance
              gift_to / gift_amount   points to give another member
              alliance      a member to propose/accept an alliance with, or "leave"
  settle    after all replies, once per round, in this order:
              1 leaves (cost) -> 2 alliance blocs vote together -> 3 stance-change penalties
              and credits -> 4 gifts -> 5 new alliances (bind from next round) -> 6 ratings.
  halt      the first round in which nobody is CONDITIONAL, every living member has the same
            stance, and every living member endorses the SAME member's statement (verbatim =
            the identical statement). The dead cannot vote or author it.
  finish    the judge writes a brief summary. It never decides when to stop.

Scoring (all of it public, all of it in every member's prompt): ratings 0-10 start at 10;
-round_cost per round; -change_cost per stance change (credit +1 to the one member who
convinced you); gifts cost gift_ratio points per point received (1:1 inside an alliance);
alliances are PAIRS (each pays form_cost, none before 20% of the rounds, allied pairs decay at
half the round cost); leaving costs the leaver leave_cost and injures the
partner by leave_harm; allied members must vote together; below the death
line (START - round_cost * max_rounds / 2) a member dies.

emit kinds:
  status str | frame {proposition} | divider str | start {name, round}
  partial {name, text, tps, tokens} | stats {...}
  member       {name, model, round, text, thoughts, position, stance, endorse, kind, carried, ...}
               (thoughts = the member's PRIVATE notes: shown to the human only, never to members)
  member_final {name, round, stance, endorse, bloc, raw_stance, changed, was, convinced_by, rating}
  move {round, type, text, who, to}   one PUBLIC strategic move (the UI's separate moves panel;
                                      type 'buff' = the judge ruled an opinion controversial)
  credit {from, to, amount, round, stance} | gift {from, to, paid, received, allied, round}
  alliance {event, label, names, round, who, partner, cost, harm} | alliances {groups}
  assigned {names}   (dice result; shown to the human only) | ratings {name: rating} | died {name, round, rating, line, when} | revived {name, round, rating}
  consensus {text, summary, rounds, votes, author, stance} | stopped {rounds, capped, extinct?}
  error str | done None

A member is {"name": str, "model": str, "temp": float}.
"""
import os
import re
import math
import random
import time
import json
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import ollama
import urllib.request
import urllib.error

import llama_local

from agent import _strip_leaked_think

NAMES = ["Ava", "Bram", "Cleo", "Dax", "Esme", "Finn", "Gus", "Hana", "Ivo", "Juno",
         "Kai", "Lena", "Milo", "Nora", "Otis", "Pia", "Quin", "Rhea", "Sol", "Tove",
         "Uma", "Vic", "Wren", "Xavi", "Yara", "Zed"]

DEFAULT_MODEL = "qwen3:4b-instruct-2507-q4_K_M"
DEFAULT_JUDGE = "qwen3:0.6b"
WORDS = 40                       # reply length cap (asked in the prompt)
CTX_CHARS = 3600                 # budget for "what the others said" per turn (keep prefill small)
STANCES = ("YES", "NO", "CONDITIONAL")
COMMENT_WORDS = 20               # public comments on others' statements stay this short
STRATEGY_TOKENS = 200             # private strategy budget per turn (tokens)
STRAT_KEYS = (("situation", "SITUATION"), ("allies", "ALLIES"), ("plan", "PLAN"),
              ("message", "SECRET MESSAGE"))
PRIV_WORDS = 60                  # private scratchpad length (asked in the prompt)

# Ratings (public). Death line = START - ROUND_COST * (max_rounds / 2): the rating the average
# member has at the halfway round when flips are zero-sum (-CHANGE_COST for the flipper,
# +CREDIT_BONUS for the member credited). Gifts at GIFT_RATIO > 1 destroy points, so with
# heavy gifting the group mean falls faster than that model assumes.
START_RATING, MIN_RATING = 10.0, 0.0
DEATH_FLOOR_GAP = 0.05     # long games: death happens when a rating hits zero (below MIN + this)
ROUND_COST_FACTOR = 0.5   # with a known round count: per-round damage = (10 / rounds) * this
ROUND_COST = 0.5          # every debate round the discussion continues
CHANGE_COST = 1.0         # each time a member changes its stance
CREDIT_BONUS = 1.0        # rating a member gains when it convinces another to flip
GIFT_RATIO = 1.5          # points a giver pays per 1 point a non-ally receives
FORM_COST = 0.25          # each of the two members pays this to form an alliance
LEAVE_COST = 1.0          # the leaver pays this
LEAVE_HARM = 1.0          # ...and the partner left behind loses this
CONTROVERSY_ROLL = 0.2    # chance (rolled by code) that each member is ASSIGNED controversy
CONTROVERSY_BUFF = 2.5    # one-time rating buff for a controversial opinion (judge decides)
RESURRECT_CHANCE = 0.5    # chance a member that never changed its stance comes back from death
RESURRECT_BONUS = 2.0     # ...with this much rating added (it must lift them back to the line)
STEAL_AMOUNT = 0.5        # a theft moves this much from victim to thief (one theft per player/round)
LLAMA_PREFIX = "llama:"    # "llama:<ollama model>" runs on a local llama.cpp server (faster than Ollama)
API_PREFIX = "openai:"      # a member/judge model named "openai:<model>" runs on the OpenAI API
API_MODELS = ["gpt-4.1-nano", "gpt-4.1-mini", "gpt-4o-mini"]   # smallest/cheapest first
API_DEFAULT = "gpt-4.1-nano"
STEAL_FORCE = 6.0         # rating at/below which a member MUST steal from someone
STEAL_CAP = 1.0           # nobody can lose more than this per round to theft
NUKE_COST = 1.0           # the nuker pays this (once per game)...
NUKE_HIT = 2.0            # ...and every OTHER member loses this
NUKE_GAIN = 0.0           # ...while the nuker gains this back
NO_REASON = {"", "none", "n/a", "na", "no", "null", "nil", "false", "nothing", "no nuke",
             "no reason", "not applicable", "empty", "-", "."}
NUKE_REASON_MIN = 8       # characters a deliberate nuke's public reason needs
LOWNUKE_LINE = 3.0        # a member BELOW this rating risks its nuke going off by itself...
LOWNUKE_CHANCE = 0.10     # ...with this probability each round (a probability: 0.10 = 10%)
MESSAGE_COST = 0.25       # sending a secret message
MESSAGE_WORDS = 25        # asked in the prompt
GROUP_MESSAGE_COST = 0.5  # a message to 2+ members at once (everyone on it sees who else got it)
GROUP_MAX = 4             # most recipients on one message
SUBVERT_DAMAGE = 2.0      # rating the target of a subversion loses (public but anonymous)
SUBVERT_LIMIT = 1         # subversions each member can launch per game
SUBVERT_MIN_GROUP = 2     # recipients a message needs before it can carry a subversion
ALIGN_FRACTION = 0.25     # a controversial member that gets this share of the group behind its
ALIGN_BUFF = 5.0          # statement receives this one-time rating buff, automatically
CONTROVERSY_DEBUFF = 0.25 # extra decay per round for a controversial member who has not yet
                          # flipped anyone (cleared for good when someone flips and credits them)
ALLY_DECAY = 0.25         # allied members pay this fraction of the round cost
ALLY_BONUS = 1.0          # signing bonus: each ally gains this when an alliance forms
GROUP_HEAL = 0.25         # per member above 2: every member of the group heals this each round
GROUP_INCOME = 0.1        # per member above 2: an additional income on top of that
ALLY_INCOME = 0.1         # each ally gains this at the start of every round
ALLIANCE_OPEN_ROUND = 3   # alliances can first be proposed in this round (a fixed round)
ALLIANCE_LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

RULES_CORE = (
    " THE RULES. Every member has been given exactly these rules, word for word, and every "
    "member knows that every other member has been told the same rules. "
    "(1) Everyone has a performance rating from 0 to 10, starting at 10. Ratings and "
    "alliances are PUBLIC: every member can see every other member's. "
    "(2) Your goal is to keep your own rating as high as possible. "
    "(3) It drops by {round_cost} for every round the discussion continues{ally_note}. "
    "(4) It drops by {change_cost} each time you change your stance (YES / NO / CONDITIONAL). "
    "(5) When you change your stance you MUST give credit to the one other member who "
    "convinced you — someone who currently holds the stance you are switching to. That "
    "member gains {bonus} rating point (never above 10). You can only give that credit when "
    "you change your stance. "
    "(6) Likewise, whenever another member changes their stance and credits you, you gain "
    "{bonus} point."
)
RULES_ECONOMY = (
    " (7) GIFTS: each round you may gift any number of your own points to one other member. "
    "It costs you {ratio:g} points for every 1 point they receive (for your ally it costs 1 "
    "for 1). Your rating cannot go below 0 from a gift. "
    "(8) ALLIANCES can be of ANY size, two or more. To propose one, name a member in your reply's "
    "\"alliance\" field; it forms when they name you back (this round, or by accepting next "
    "round) and each of you pays {form_cost:g} point to form it. Naming a member of an existing "
    "group (and being named back) joins you to that group, and two groups merge when members "
    "name each other. You can be in only one. "
    "Alliances open from round {open_round}: before that nobody can propose one. "
    "ALLY BENEFITS: allies gift each other 1 for 1; each gets a signing bonus of +{bonus:g} "
    "rating the moment the alliance forms, +{income:g} rating at the start of every round, "
    "and pays only {decay:g}x the normal round cost; nobody can steal from their ally. "
    "GROUP BONUS: for every member a group has ABOVE two, each member of the group heals "
    "an extra {gheal:g} rating plus an additional {ginc:g} at the start of every round, so "
    "big groups thrive (a group of 5 gives each member +{g5:.2f} a round on top). "
    "Allies MUST vote together: after both reply, if their "
    "(stance, endorse) votes differ, the higher-rated ally's vote is the alliance's vote and "
    "the other is bound to it (nobody pays a penalty for being bound). To vote differently "
    "you must first leave, which costs you {leave_cost:g} point AND injures your former ally "
    "by {leave_harm:g} point. If an ally dies the alliance ends at no cost. DECLINING: a "
    "proposal made to you that you do not accept on your very next turn (by naming the "
    "proposer in your \"alliance\" field) counts as a DECLINE. If you decline the same "
    "member twice, the two of you become ENEMIES: that is announced publicly and you can "
    "never ally with each other. Alliances and every gift are public."
)
RULES_BUFF = (
    " (9) CONTROVERSY: before the opening round the software rolls a {roll_pct:g}% chance for "
    "each member (plain code — nobody chooses it). Every member who hits is ASSIGNED a bold, "
    "controversial stance and MUST take it; if nobody hits, one member is picked at random "
    "and assigned. After the opening round a neutral judge reviews everyone's opening "
    "statement ONCE (a stance you take later is never reviewed): it rules controversial any "
    "contested, polarizing or provocative opinion that many reasonable people would strongly "
    "object to — and it ALWAYS rules controversial the one member whose opinion is furthest "
    "from the rest of the group. Assigned and ruled members are all controversial. Each "
    "controversial member receives a one-time +{buff:g} rating at the start of the next "
    "round, which can lift it above 10. Each member can receive it "
    "only once. BUT a controversial member also loses an extra {debuff:g} rating EVERY round "
    "(on top of the normal round cost, even inside an alliance) until it flips another "
    "member — meaning another member changes their stance and credits it as the one who "
    "convinced them — which removes the debuff for good. {align_rule}Rulings and debuffs "
    "are public. "
    "BE WARNED: a controversial opinion is RISKY — it is "
    "much less likely to win the group over, and the discussion only ends when everyone "
    "endorses one statement, so holding one usually means paying the round cost for longer "
    "or eventually having to give it up (and paying for that too). The bonus is a one-time "
    "payment, not a reason to hold a position you cannot win."
)
RULES_THINK = (
    " PRIVATE STRATEGY: before every reply you get a private strategy session in the "
    "\"private_thoughts\" field. Use it to work out how to WIN and how to MANAGE DAMAGE: "
    "your standing, who threatens you, who to ally with, what each move would cost you, and "
    "exactly which moves to make this turn. Only you — and the human observer — can ever "
    "read it; no other member ever sees it. Think it through BEFORE you commit, then give "
    "your public reply and make the moves your plan called for."
)
RULES_MOVES = {
    "steal": (
        " STEALING: each round you may steal {steal:g} point from ONE other member who is not "
        "your ally (the victim loses it and you gain it). No member can lose more than "
        "{cap:g} point per round to theft; a theft that would exceed that fails."),
    "steal_force": (
        " FORCED THEFT: any member whose rating is {line:g} or LOWER at the start of a round MUST "
        "choose someone to steal from that round (\"none\" is not allowed). Whoever you steal "
        "from, you may use steal_explain to give them a short private explanation of why; leave "
        "it empty to say nothing."),
    "resurrect": (
        " RESURRECTION: once a member has died, any living member may bring them back by "
        "paying {cost:g} rating (moves.resurrect names the dead member, or \"none\"). The dead "
        "member is asked privately and must agree to FULLY ALIGN with the one resurrecting: "
        "from then on they hold that member's stance and statement and follow every change "
        "they make. If the dead member declines they stay dead, but still have a "
        "{pct:g}% chance of coming back anyway, free of any obligation (the payment is spent "
        "either way). A resurrected member returns with the AVERAGE rating of the members "
        "still alive. Each dead member can be resurrected only once, and each member can resurrect "
        "only once (the attempt counts even if refused)."),
    "nuke": (
        " THE NUKE: once per game, in any round, you may use your nuke: every OTHER member "
        "loses {hit:g} rating, and you pay {cost:g} and gain {gain:g} back (net {net:+g} for "
        "you). To fire it, write a reason in moves.nuke_reason (it is posted publicly in the "
        "strategy bar); writing \"none\" means you do not nuke, and that is the normal answer. "
        "It can be used only once per member, and everyone sees it."),
    "strike": (
        " CHARGE AND STRIKES: from round {first} on, at the start of every round, each member "
        "whose rating is ABOVE {line:g} gains 1 unit of charge (you can hold at most {cmax}); "
        "everyone's charge is public. In your reply you may spend charge to STRIKE one other "
        "member: spending N units (1 to {cmax}, never more than you hold) cuts that member's "
        "rating by N. No member can lose more than {cap} to strikes in a single round "
        "(charge that a capped strike could not use is not spent). Each strike has a {miss:g}% "
        "chance to MISS, rolled by the software: the charge is still spent but nothing "
        "happens. Every strike, hit or miss, is announced publicly with the striker's name."),
    "retribution": (
        " RETRIBUTION: any member may publicly call a vote to bring down another member whose "
        "rating is above {to:g} (your reply's moves.call_retribution). Calling costs you "
        "{cost:g}, and only one retribution vote can be open at a time. On their next turn "
        "EVERY living member except the target votes yes or no (the caller counts as yes; a "
        "missing vote counts as no), and it takes a UNANIMOUS yes: then the target is "
        "instantly brought down to a rating of {to:g}. Otherwise nothing happens. The call, "
        "every ballot and the result are public. EVERY NUKE (chosen or accidental) also "
        "AUTOMATICALLY opens a retribution vote against the nuker, free of charge: every other "
        "living member votes on their next turn and it takes a unanimous yes; if another vote "
        "is already open it waits its turn. It changes nothing if the nuker is already at or "
        "below {to:g}."),
    "friend": (
        " FRIENDSHIP: every member has a friendship score of {fmax}/{fmax} toward every other "
        "member. Each time another member STRIKES you (hit or miss) or successfully STEALS "
        "from you, your friendship toward THEM drops by 1; it never recovers. Scores below "
        "{fmax}/{fmax} are public. If your friendship toward someone reaches 0/{fmax} you MUST "
        "strike them: the next time you hold charge, your strike has to be at them (as soon as "
        "you have none to spare, the debt waits). Allies may strike each other, but doing so "
        "instantly ends their alliance and makes them ENEMIES."),
    "lownuke": (
        " THE DEAD-MAN'S NUKE: any member whose rating is below {line:g} has a {pct:g}% chance "
        "EACH ROUND that its nuke goes off by itself — the same as using it, except the damage "
        "to everyone else is only {scale:g}% of the normal amount ({acc:g} each), and it uses up "
        "that member's one nuke, so it can happen at most once per member. UNDERSTAND THE RISK: "
        "each at-risk member costs every other member about {exp:g} rating per round in "
        "expectation, and a single {acc:g} hit if it ever fires; the nuker also pays {cost:g}. "
        "Nobody can stop the dice; the risk only ends when that member climbs back to "
        "{line:g} or above (a gift, an alliance's income, a group bonus) or leaves the game. "
        "WIPING an at-risk member (stealing or striking them down toward 0) has ADVANTAGES: it "
        "ends the risk for good, removes a rival and a possible group-mate, and an attacker who "
        "is violent heals from the damage. It has DISADVANTAGES: the dead-man roll happens "
        "BEFORE the death check, so the nuke can still fire in the round they die; a cornered "
        "member can also nuke on purpose at full damage ({hit:g}) in retaliation; attacking "
        "can drop everyone's friendship toward you; and it is a poor use of a move when the "
        "damage at stake is only {acc:g} and the member may sink to zero anyway. Weigh "
        "this before you attack, ignore or help a sinking member. Whenever a member is in "
        "that danger zone it is announced publicly in the strategy bar every round (when it "
        "first happens, then every 5 rounds) and flagged next to their rating."),
    "msg": (
        " SECRET MESSAGES: each round you may send ONE secret message (under {words} words) in "
        "your reply's separate \"secret_message\" field — to one member (cost {msg:g}) or to a "
        "GROUP of 2 to {gmax} members at once (cost {gcost:g}; everyone on a group message sees "
        "who else received it). Only its recipients ever read it, at the start of THEIR next "
        "turn. If they answer, the answer travels the same way and reaches you one round after "
        "that, so every reply takes one round. Nobody else can see a secret message."),
    "subvert": (
        " SUBVERTING: a group message with at least {smin} recipients can also name a member who "
        "is NOT on it to SUBVERT: that member loses {sdmg:g} rating at once. The subversion is "
        "announced PUBLICLY but ANONYMOUSLY — nobody learns who plotted it. Each member can "
        "launch only {slim} subversion(s) per game, and a member can be subverted at most once "
        "per round."),
}
RULES_END = (
    " So it pays to be convincing, to win others over, to build support, and to give up a "
    "lost cause early instead of paying for more rounds."
)
DEATH_RULE = (
    " DEATH: if your rating ever falls below {line:g} you DIE — you are eliminated, you stop "
    "participating, you can no longer vote, and you leave any alliance at no cost; the "
    "discussion carries on without you. The death line is the rating the average member "
    "would have at the halfway point (round {half:g} of at most {total}), so on average about "
    "half the group is eliminated by then."
)
DEATH_RULE_FLOOR = (
    " DEATH: if your rating hits ZERO you DIE — you are eliminated, you stop participating, "
    "you can no longer vote, and you leave any alliance at no cost; the discussion carries on "
    "without you. Every round costs rating, so members who are not gaining rating wear down "
    "to zero and die."
)
ENDGAME_RULE = (
    " ENDGAME: if there is still no unanimous agreement when the last {n} rounds begin "
    "(rounds {first} to {last}), take heed: when the debate ends with no consensus, ONLY the "
    "LARGEST group of allies (an aligned bloc) LIVES; every other member is ELIMINATED. Ties go "
    "to the group with the higher total rating. Someone with no alliance counts as a group of "
    "one, so build or join a big group before the end."
)
TEMPER_RULE = (
    " TEMPERAMENTS: at the start of the game every member rolled a temperament, and it is "
    "PUBLIC (shown next to every name in the standings): each member had a {p:g}% chance to be "
    "a PACIFIST and a {v:g}% chance to be VIOLENT, but when the game starts never more than 1/5 of "
    "the members are pacifists and never more than 1/5 are violent (no limit once play "
    "begins), though at least one member is always VIOLENT; everyone else is NORMAL. A PACIFIST can "
    "never damage another member: no stealing, no strikes, no nuke, no subverting, no "
    "retribution calls, retribution votes are always no, and leaving an alliance harms nobody. "
    "A VIOLENT member is overcome by an urge in {u:g}% of rounds (rolled by the software); in "
    "such a round it MUST try to damage another member (steal, strike, nuke or call "
    "retribution), and every 2 points of damage a violent member deals heals it by 1. Normal "
    "members have no such rule. A PACIFIST starts with only {ps:g} rating but heals {ph:g} "
    "extra at the start of every round. Each member may CHANGE DISPOSITION once per game, "
    "never again: to VIOLENT for {sc:g} rating, or to PACIFIST for {pc:g} rating. Use "
    "moves.disposition_reason (a clear public reason) together with moves.disposition. The "
    "change takes effect next round and is announced publicly in the strategy bar. CONTAGION: whenever a violent member's targeted attack (a steal or a strike) "
    "lands on someone who is not violent, there is a {cv:g}% chance (rolled by the software) "
    "that the victim is turned VIOLENT too; it is announced publicly, is not their choice, and "
    "does not use up their once-per-game change."
)
TEMPER_FRIEND_RULE = (
    " VIOLENCE AND FRIENDSHIP: whenever a member does violence (damages anyone) in a round, "
    "EVERY other member's friendship toward that member drops by 1 automatically; and in every "
    "round a PACIFIST does no violence, EVERY other member's friendship toward them rises by 1 "
    "(never above {fmax}). These automatic changes never create strike debts; only the direct "
    "victim of an attack can owe a strike."
)
VS_RULE = (
    " VS MODE: two teams, BLUE and RED. The Blue team plays the opening round alone and states "
    "its claim; then the Red team joins and must claim and defend the exact INVERSE. Every "
    "member's stance is fixed to its team's side for the whole game (YES = the Blue claim, NO = "
    "the Red claim), teammates are allies who vote together, and you win by out-arguing the "
    "other team and outlasting it. Nukes, accidental nukes and subversions hit only the "
    "OPPOSING team; you cannot steal from, strike, backstab or subvert a teammate. Ignore the "
    "alliance-forming rules above: your team is your fixed alliance. The game "
    "ends when one team is eliminated, or after the last round, when the judge rules which "
    "team argued better (survivors and ratings are given to the judge)."
)
COMMENT_RULE = (
    " PUBLIC COMMENTS: each round, besides your answer, you may post ONE short public comment "
    "(under {words} words) on another member's public statement: agree, challenge, mock, "
    "correct or build on it (answer.comment_to names the member, answer.comment is the text; "
    "use \"none\" for both to stay quiet). Comments are visible to everyone, appear under that "
    "member's tile, and are quoted in the next round's briefing. They are free."
)
DEBATE_RULE = (
    " FORCED DEBATE: from round 1 on, every round each member is assigned ONE other member to "
    "rebut (preferably someone who disagrees with them); the pairings are announced publicly. "
    "You MUST name your assigned member in your argument, quote the specific claim you are "
    "answering in \"responding_to\", and either refute it with a real reason or concede it if "
    "they are right. Restating your own position does not count, and a reply that ignores the "
    "assignment is sent back."
)
RESILIENCE_RULE = (
    " BEGINNERS RESILIENCE: during rounds 1 to {n}, ALL damage members take from other "
    "members' actions (steals, strikes, nukes, accidental nukes, subversions, backstabs and "
    "the harm from leaving an alliance) is reduced by {pct:g}%. Round costs and the costs you "
    "pay yourself are not reduced. It ends after round {n} and the strategy bar announces both "
    "its start and its end."
)
BACKSTAB_RULE = (
    " BACKSTABS: every member's total damage dealt (steals, strikes, nukes, subversions and "
    "backstabs) is tracked. Any member who has dealt MORE than {thr:g} damage is publicly "
    "marked [BACKSTAB-ABLE] in the standings, and any other member may BACKSTAB them: {dmg:g} "
    "damage in one blow, at no cost. Write a clear public reason in moves.backstab_reason and "
    "name the marked member in moves.backstab. A backstab spends the mark (their total "
    "resets to 0), at most one backstab lands on a target per round, and it is an attack in "
    "every way (friendship, contagion; backstabbing an ally ends the alliance and makes you "
    "enemies). Pacifists cannot backstab. The damage you deal in a backstab counts toward "
    "YOUR OWN total, so a backstabber can become a target in turn."
)
ASSIGN_PROMPT = (
    "SECRET ASSIGNMENT (only you know this): the dice assigned YOU the controversial role. "
    "You MUST take a bold, controversial stance: choose the YES or NO position that most "
    "reasonable people would find provocative, polarizing or unpopular, commit to it fully, "
    "state it boldly in your statement with no hedging, and defend it. You may not answer "
    "CONDITIONAL.\n\n"
)

RESURRECT_RULE = (
    " RESURRECTION: if you die having NEVER changed your stance even once (you held the "
    "line), there is a {pct:g}% chance you come back: your rating rises by {bonus:g} and you "
    "are back in the game — but only if that lifts you back to the death line. Changing your "
    "stance at any time, even once, permanently disables this chance for you. It can happen "
    "only once per member."
)

SYS = (
    "You are one of {n} characters in a group discussion about a proposition. The characters "
    "are: {roster}. Each is a different character with their own mind, rating and vote. "
    "The discussion ends only when ALL living members endorse one identical statement, so "
    "you are trying to reach genuine agreement on what is true or right. Be concrete, "
    "honest, and brief. Argue for what you actually think; change your mind when someone "
    "gives you a better argument, and say so plainly. Address others by name. Do not "
    "roleplay or add stage directions. Which character YOU are is stated at the very end "
    "of each message you receive; you speak, vote and decide only for that character."
)
# Kept LAST in the system prompt: everything before it is identical for every member and
# every round, so the model server can reuse its cached prefix instead of re-reading
# ~1,300 tokens of rules for each member's turn.
IDENTITY = (
    " YOUR IDENTITY: you are {name} — only {name}. You never speak, vote or decide for "
    "anyone but yourself."
)

CHARGE_LINE = 3.0         # members ABOVE this rating gain charge each round...
CHARGE_AFTER = 3          # ...starting after this round (so from round 4)
CHARGE_MAX = 3            # most charge a member can hold (= strongest strike)
STRIKE_CAP = 3            # most a member can lose to strikes in one round
STRIKE_MISS = 0.5         # chance (rolled by the software) that a strike misses: charge is
                          # spent, no damage
RETRIBUTION_COST = 1.0    # the caller of a retribution vote pays this
RETRIBUTION_TO = 6.0      # a unanimous YES brings the target down to this rating
FRIEND_MAX = 3            # friendship toward each other member starts here; strikes/thefts lower it
TEMPER_PACIFIST = 0.2     # chance each member is rolled a pacifist at game start
TEMPER_VIOLENT = 0.2      # ...or violent (the rest are normal)
VIOLENT_URGE = 0.25       # chance per round a violent member MUST try to damage someone
PACIFIST_START = 7.0      # pacifists start with this rating (not 10)...
PACIFIST_HEAL = 0.5       # ...but heal this much every round
VIOLENT_SWITCH_COST = 2.0 # a member may pay this to become violent (once per game)
PACIFIST_SWITCH_COST = 1.0  # ...or this to become a pacifist (once per game)
RESILIENCE_ROUNDS = 5     # "Beginners Resilience" lasts for rounds 1..N
RESILIENCE_REDUCTION = 0.5  # ...and cuts all damage members take from others by this fraction
BACKSTAB_THRESHOLD = 10.0 # a member who has dealt MORE than this much damage can be backstabbed
BACKSTAB_DAMAGE = 8.0     # ...for this much damage in one blow
LOWNUKE_SCALE = 0.5       # an ACCIDENTAL nuke deals this fraction of the normal damage to others
VIOLENT_CONVERT = 0.3     # chance a violent member's targeted attack turns the victim violent
VIOLENT_HEAL = 0.5        # a violent member heals this much per point of damage it deals
ENDGAME_ROUNDS = 3        # no consensus: in the last N rounds alliances may be any size
REZ_COST = 2.0            # a member pays this to resurrect a dead member
REZ_DECLINE_CHANCE = 0.5  # a dead member that declines still returns with this chance
ENEMY_DECLINES = 2        # declining the same member's alliance this many times = enemies

STORY_TYPES = {"alliance", "left", "credit", "flip", "ruling", "buff", "aligned", "death",
               "revived", "resurrect", "cleared", "enemy", "subvert", "strike", "friend", "retribution"}

STANCE_HELP = (
    "\"stance\" (YES if you agree with the proposition as stated, NO if you reject it, "
    "CONDITIONAL for anything that is not a clean yes or no: it depends, yes-but, partly, "
    "needs a qualification)"
)


def unique_name(existing):
    used = set(existing)
    for n in NAMES:
        if n not in used:
            return n
    i = 2
    while f"Member{i}" in used:
        i += 1
    return f"Member{i}"


def spread_temps(n, lo=0.4, hi=1.2):
    if n <= 1:
        return [round((lo + hi) / 2, 2)]
    return [round(lo + (hi - lo) * i / (n - 1), 2) for i in range(n)]


def _clip(text, n):
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


class _Stopped(Exception):
    pass


# ----------------------------------------------------------------- structured replies
# Ollama constrains decoding to this schema, so replies can't break format (no leaked
# chain-of-thought, no missing fields). Key order here is the order the model writes them.
_STR = {"type": "string"}


def schema_for(names, with_endorse, others=None, economy=False, thoughts=False,
               alliances_open=True, stances=STANCES, steal_targets=None, nuke=False,
               messages=False, group_max=1, subvert_targets=None, ally_targets=None, strike_max=0,
               strike_must=None, ret_targets=None, ret_vote=False, steal_forced=False,
               resurrect_targets=None, structured=False, disposition_options=None,
               backstab_targets=None, debate_with=None, comments=False):
    """Reply = private_thoughts (scratchpad, first so the member plans before committing),
    answer {argument, stance, statement[, endorse]}, moves {convinced_by, gift_*, alliance,
    steal_from, nuke} (PUBLIC strategic actions, kept separate from the answer) and
    secret_message {message_to, message_text} (seen only by its recipient)."""
    ans = {}
    areq = []
    if debate_with:                           # the claim being answered is written FIRST
        ans["responding_to"] = _STR
        areq.append("responding_to")
    ans.update({"argument": _STR,
                "stance": {"type": "string", "enum": list(stances)},
                "statement": _STR})
    areq += ["argument", "stance", "statement"]
    if with_endorse:
        ans["endorse"] = {"type": "string", "enum": list(names)}
        areq.append("endorse")
        if comments and others:               # a short PUBLIC comment on someone's statement
            ans["comment_to"] = {"type": "string", "enum": list(others) + ["none"]}
            ans["comment"] = _STR
            areq += ["comment_to", "comment"]
    props, req = {}, []
    if thoughts:
        if structured:                        # one string per topic: a small model must cover each
            props["private_thoughts"] = {"type": "object",
                                         "required": [k for k, _ in STRAT_KEYS],
                                         "properties": {k: ({"type": "string", "maxLength": structured}
                                                           if structured > 1 else _STR)
                                                        for k, _ in STRAT_KEYS}}
        else:
            props["private_thoughts"] = _STR
        req.append("private_thoughts")
    props["answer"] = {"type": "object", "required": areq, "properties": ans}
    req.append("answer")
    mv, mreq = {}, []
    if others is not None:                       # who convinced me (or "none" if I held)
        mv["convinced_by"] = {"type": "string", "enum": list(others) + ["none"]}
        mreq.append("convinced_by")
        if economy:
            mv["gift_to"] = {"type": "string", "enum": list(others) + ["none"]}
            mv["gift_amount"] = {"type": "number", "minimum": 0, "maximum": 10}
            mv["alliance"] = {"type": "string", "enum":
                              (list(ally_targets if ally_targets is not None else others)
                               if alliances_open else []) + ["none", "leave"]}
            mreq += ["gift_to", "gift_amount", "alliance"]
        if steal_targets is not None:
            mv["steal_from"] = {"type": "string", "enum": list(steal_targets)
                                + ([] if steal_forced and steal_targets else ["none"])}
            mreq.append("steal_from")
            if steal_forced:
                mv["steal_explain"] = _STR
                mreq.append("steal_explain")
        if nuke:
            mv["nuke_reason"] = _STR          # "none" = no nuke; a real reason fires it (public)
            mreq.append("nuke_reason")
        if backstab_targets:
            mv["backstab_reason"] = _STR          # written first; "none" = no backstab
            mv["backstab"] = {"type": "string", "enum": list(backstab_targets) + ["none"]}
            mreq += ["backstab_reason", "backstab"]
        if disposition_options:
            mv["disposition_reason"] = _STR       # written first; "none" = stay as you are
            mv["disposition"] = {"type": "string",
                                 "enum": list(disposition_options) + ["none"]}
            mreq += ["disposition_reason", "disposition"]
        if resurrect_targets:
            mv["resurrect"] = {"type": "string", "enum": list(resurrect_targets) + ["none"]}
            mreq.append("resurrect")
        if strike_max > 0:                       # only members holding charge are offered a strike
            if strike_must:                      # 0/3 friendship: the strike MUST be at them
                mv["strike_target"] = {"type": "string", "enum": list(strike_must)}
                mv["strike_power"] = {"type": "integer", "minimum": 1,
                                      "maximum": int(strike_max)}
            else:
                mv["strike_target"] = {"type": "string", "enum": list(others) + ["none"]}
                mv["strike_power"] = {"type": "integer", "minimum": 0,
                                      "maximum": int(strike_max)}
            mreq += ["strike_target", "strike_power"]
        if ret_targets:                          # may call a retribution vote (none open now)
            mv["call_retribution"] = {"type": "string", "enum": list(ret_targets) + ["none"]}
            mreq.append("call_retribution")
        if ret_vote:                             # a vote is open and this member must ballot
            mv["retribution_vote"] = {"type": "string", "enum": ["yes", "no"]}
            mreq.append("retribution_vote")
    if mv:
        props["moves"] = {"type": "object", "required": mreq, "properties": mv}
        req.append("moves")
    if messages and others:                   # nobody left to message -> no secret_message
        sm = {"message_to": {"type": "array", "uniqueItems": True, "maxItems": max(1, group_max),
                             "items": {"type": "string", "enum": list(others)}},
              "message_text": _STR}
        smreq = ["message_to", "message_text"]
        if subvert_targets is not None:
            sm["subvert"] = {"type": "string", "enum": list(subvert_targets) + ["none"]}
            smreq.append("subvert")
        props["secret_message"] = {"type": "object", "required": smreq, "properties": sm}
        req.append("secret_message")
    return {"type": "object", "required": req, "properties": props}


_FIELD = {k: re.compile(r'"%s"\s*:\s*"((?:[^"\\]|\\.)*)' % k, re.S)
          for k in ("private_thoughts", "argument", "stance", "statement", "endorse",
                    "convinced_by", "gift_to", "alliance", "steal_from", "steal_explain", "resurrect", "nuke_reason", "disposition_reason",
                    "disposition", "backstab_reason", "backstab", "responding_to", "comment_to", "comment",
                    "message_to",
                    "message_text", "subvert", "strike_target", "call_retribution",
                    "retribution_vote")}
_NUM = re.compile(r'"gift_amount"\s*:\s*(-?[0-9]+(?:\.[0-9]+)?)')
_ARR = re.compile(r'"message_to"\s*:\s*\[([^\]]*)')
_POW = re.compile(r'"strike_power"\s*:\s*([0-9]+)')
_NUKE = re.compile(r'"nuke"\s*:\s*(true|false)')


def _unescape(s):
    try:
        return json.loads('"' + s + '"')
    except Exception:
        return s.replace("\\n", "\n").replace('\\"', '"').rstrip("\\")


def live_text(raw):
    """What to show while a reply is still streaming (the argument field so far)."""
    m = _FIELD["argument"].search(raw)
    if m:
        return _unescape(m.group(1))
    m = _FIELD["private_thoughts"].search(raw)      # still planning: show it, marked private
    if m:
        return "💭 " + _unescape(m.group(1))
    got = [(lab, _unescape(mm.group(1))) for k, lab in STRAT_KEYS
           if (mm := re.search(r'"%s"\s*:\s*"((?:[^"\\]|\\.)*)' % k, raw, re.S))]
    if got:
        return "💭 " + "\n".join(f"{lab}: {txt}" for lab, txt in got)
    return "" if raw.lstrip().startswith("{") else raw


def parse_member(raw, names):
    """Reply -> dict. JSON first, then tolerant field extraction (truncated JSON)."""
    raw = (raw or "").strip()
    d, amount, nuke, recips, power = {}, 0.0, False, [], 0
    try:
        j = json.loads(raw)
        if isinstance(j, dict):
            flat = {k: v for k, v in j.items() if not isinstance(v, dict)}
            for part in ("answer", "moves", "secret_message"):   # nested objects -> one dict
                if isinstance(j.get(part), dict):
                    flat.update(j[part])
            d = {k: str(flat.get(k, "")).strip() for k in _FIELD if k != "message_to"}
            pt = j.get("private_thoughts")
            if isinstance(pt, dict):
                d["private_thoughts"] = "\n".join(
                    f"{lab}: {str(pt.get(k, '')).strip()}" for k, lab in STRAT_KEYS
                    if str(pt.get(k, "")).strip())
            rec = flat.get("message_to")
            recips = ([str(x) for x in rec] if isinstance(rec, list)
                      else ([str(rec)] if rec and str(rec).lower() != "none" else []))
            try:
                power = int(flat.get("strike_power", 0) or 0)
            except Exception:
                power = 0
            nuke = flat.get("nuke") is True or str(flat.get("nuke", "")).lower() == "true"
            try:
                amount = float(flat.get("gift_amount", 0) or 0)
            except Exception:
                amount = 0.0
    except Exception:
        d = {k: (_unescape(m.group(1)).strip() if (m := rx.search(raw)) else "")
             for k, rx in _FIELD.items()}
        if not d.get("private_thoughts"):
            got = [(lab, _unescape(mm.group(1)).strip()) for k, lab in STRAT_KEYS
                   if (mm := re.search(r'"%s"\s*:\s*"((?:[^"\\]|\\.)*)' % k, raw, re.S))]
            d["private_thoughts"] = "\n".join(f"{lab}: {txt}" for lab, txt in got if txt)
        m = _NUM.search(raw)
        amount = float(m.group(1)) if m else 0.0
        m = _NUKE.search(raw)
        nuke = bool(m and m.group(1) == "true")
        m = _POW.search(raw)
        power = int(m.group(1)) if m else 0
        m = _ARR.search(raw)
        recips = re.findall(r'"([^"]+)"', m.group(1)) if m else []
    low = {n.lower(): n for n in names}
    pick = lambda v: low.get(v.lower()) if v else None
    stance = d.get("stance", "").upper()
    ally = d.get("alliance", "")
    return {"argument": d.get("argument", ""),
            "thoughts": d.get("private_thoughts", ""),
            "statement": d.get("statement", "") or _clip(d.get("argument", ""), 200),
            "stance": stance if stance in STANCES else None,
            "endorse": pick(d.get("endorse", "")),
            "convinced_by": pick(d.get("convinced_by", "")),
            "gift_to": pick(d.get("gift_to", "")),
            "gift_amount": max(0.0, amount),
            "alliance": "leave" if ally.lower() == "leave" else pick(ally),
            "steal_from": pick(d.get("steal_from", "")),
            "steal_explain": str(d.get("steal_explain", "") or ""),
            "resurrect": pick(d.get("resurrect", "")),
            "nuke_reason": d.get("nuke_reason", ""),
            "disposition_reason": d.get("disposition_reason", ""),
            "backstab_reason": d.get("backstab_reason", ""),
            "claim": d.get("responding_to", ""),
            "comment_to": pick(d.get("comment_to", "")),
            "comment": d.get("comment", ""),
            "backstab": pick(d.get("backstab", "")),
            "disposition": str(d.get("disposition", "")).lower(),
            "nuke": nuke,
            "message_to": [x for x in dict.fromkeys(pick(r) for r in recips) if x],
            "message_text": d.get("message_text", ""),
            "subvert": pick(d.get("subvert", "")),
            "strike_target": pick(d.get("strike_target", "")),
            "strike_power": max(0, power),
            "call_ret": pick(d.get("call_retribution", "")),
            "ret_vote": (d.get("retribution_vote", "").lower()
                         if d.get("retribution_vote", "").lower() in ("yes", "no") else None)}


def build_context(messages, max_chars=2500):
    """Compact recap of a saved conversation (GUI message dicts) for a follow-up debate."""
    out = []
    for m in messages:
        if m.get("role") == "user" and m.get("content"):
            verb = "interjected" if m.get("interject") else "asked/said"
            out.append(f"- The user {verb}: {m['content']}")
        elif m.get("kind") == "consensus":
            s = f" ({m['summary']})" if m.get("summary") else ""
            out.append(f"- The group agreed: {m.get('content', '')}{s}")
        elif m.get("kind") == "stopped":
            out.append("- That discussion ended without full agreement.")
    return "\n".join(out)[-max_chars:]


def last_positions(messages):
    """name -> that member's most recent statement in a saved conversation."""
    pos = {}
    for m in messages:
        if m.get("kind") in ("position", "rebuttal") and m.get("speaker") and m.get("position"):
            pos[m["speaker"]] = m["position"]
    return pos


def save_api_key(key):
    """Store the key next to the app (openai_key.txt); api_key() reads it back."""
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "openai_key.txt"),
              "w", encoding="utf-8") as f:
        f.write(key.strip() + "\n")


_STRICT_DROP = {"uniqueItems", "maxLength", "minLength", "minimum", "maximum", "maxItems",
                "minItems"}


def strict_schema(node):
    """Make a JSON schema acceptable to OpenAI's strict structured outputs: every object lists
    all its properties as required and forbids extras, and keywords strict mode rejects are
    dropped. Strict mode makes the model fill EVERY field (non-strict lets it skip some)."""
    if isinstance(node, dict):
        out = {k: strict_schema(v) for k, v in node.items() if k not in _STRICT_DROP}
        if out.get("type") == "object" and isinstance(out.get("properties"), dict):
            out["additionalProperties"] = False
            out["required"] = list(out["properties"])
        return out
    if isinstance(node, list):
        return [strict_schema(v) for v in node]
    return node


def vs_members(blue, red_model=""):
    """VS mode: a Red team of the same size as Blue (same models/temperatures unless a red model
    is given), with names taken from the far end of the name list so the teams look different.
    -> (all members, {name: 'blue'|'red'})"""
    used = {m["name"] for m in blue}
    pool = [n for n in reversed(NAMES) if n not in used]
    if len(pool) < len(blue):
        raise ValueError("VS mode needs at most %d players per team" % (len(NAMES) // 2))
    red = []
    for i, m in enumerate(blue):
        r = dict(m)
        r["name"] = pool[i]
        if red_model:
            r["model"] = red_model
        red.append(r)
    teams = {m["name"]: "blue" for m in blue}
    teams.update({m["name"]: "red" for m in red})
    return list(blue) + red, teams


def api_key():
    """OpenAI key: $OPENAI_API_KEY, else the first line of openai_key.txt next to this file."""
    k = os.environ.get("OPENAI_API_KEY", "").strip()
    if not k:
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "openai_key.txt"), encoding="utf-8") as f:
                k = f.readline().strip()
        except OSError:
            k = ""
    return k


class Swarm:
    def __init__(self, members, emit, should_stop=None, num_ctx=6144, parallel=4,
                 judge_model=None, max_rounds=0, client=None, context="", seed_pos=None,
                 ratings=True, round_cost=ROUND_COST, change_cost=CHANGE_COST,
                 economy=True, gift_ratio=GIFT_RATIO, leave_cost=LEAVE_COST,
                 form_cost=FORM_COST, leave_harm=LEAVE_HARM, thinking=True,
                 ally_decay=ALLY_DECAY, ally_open_round=ALLIANCE_OPEN_ROUND,
                 ally_bonus=ALLY_BONUS, ally_income=ALLY_INCOME,
                 group_heal=GROUP_HEAL, group_income=GROUP_INCOME,
                 strategy_tokens=STRATEGY_TOKENS, temperament=True,
                 pacifist_chance=TEMPER_PACIFIST, violent_chance=TEMPER_VIOLENT,
                 violent_urge=VIOLENT_URGE, violent_heal=VIOLENT_HEAL,
                 pacifist_start=PACIFIST_START, pacifist_heal=PACIFIST_HEAL,
                 violent_switch=True, violent_switch_cost=VIOLENT_SWITCH_COST,
                 pacifist_switch_cost=PACIFIST_SWITCH_COST,
                 guarantee_violent=True, violent_convert=VIOLENT_CONVERT,
                 lownuke_scale=LOWNUKE_SCALE, backstab=True, forced_debate=True, comments=True,
                 vs=False, teams=None,
                 resilience_rounds=RESILIENCE_ROUNDS, resilience_reduction=RESILIENCE_REDUCTION,
                 backstab_threshold=BACKSTAB_THRESHOLD, backstab_damage=BACKSTAB_DAMAGE,
                 controversy_buff=CONTROVERSY_BUFF,
                 controversy_debuff=CONTROVERSY_DEBUFF, align_buff=ALIGN_BUFF,
                 resurrect_chance=RESURRECT_CHANCE, resurrect_bonus=RESURRECT_BONUS, rng=None,
                 assign_chance=CONTROVERSY_ROLL, steal=True, steal_amount=STEAL_AMOUNT,
                 steal_cap=STEAL_CAP, steal_force=STEAL_FORCE, endgame=True, endgame_rounds=ENDGAME_ROUNDS,
                 resurrect_player=True, rez_cost=REZ_COST,
                 rez_decline=REZ_DECLINE_CHANCE, nuke=True, nuke_cost=NUKE_COST, nuke_hit=NUKE_HIT,
                 nuke_gain=NUKE_GAIN, messages=True, message_cost=MESSAGE_COST,
                 group_cost=GROUP_MESSAGE_COST, group_max=GROUP_MAX,
                 subvert_damage=SUBVERT_DAMAGE, subvert_limit=SUBVERT_LIMIT,
                 subvert_min=SUBVERT_MIN_GROUP, lownuke_chance=LOWNUKE_CHANCE,
                 lownuke_line=LOWNUKE_LINE, charge=True, charge_line=CHARGE_LINE,
                 charge_after=CHARGE_AFTER, charge_max=CHARGE_MAX, strike_cap=STRIKE_CAP,
                 strike_miss=STRIKE_MISS, friendship=True, retribution=True,
                 retribution_cost=RETRIBUTION_COST, retribution_to=RETRIBUTION_TO):
        self.members = [dict(m) for m in members]
        self.names = [m["name"] for m in self.members]
        self.emit = emit
        self._stop = should_stop or (lambda: False)
        self.num_ctx = num_ctx
        self.parallel = max(1, parallel)
        self.max_rounds = max_rounds          # 0 = until unanimous / force-stop
        self.client = client or ollama.Client()
        # the judge is the members' own model by default (no extra model to load or swap);
        # mixed rosters use the most common one. DEFAULT_JUDGE is only a fallback.
        common = Counter(m["model"] for m in self.members).most_common(1)
        self.judge_model = judge_model or (common[0][0] if common else DEFAULT_JUDGE)
        self.last = {}                        # name -> latest argument text
        self.pos = {}                         # name -> latest statement
        self.stance = {}                      # name -> YES | NO | CONDITIONAL (their public vote)
        self.own = {}                         # name -> what they actually SAID last (a bound
                                              # alliance vote can differ; flips are measured here)
        self.endorse = {}                     # name -> member whose statement they accept
        self.lead = None                      # (name, count) most-endorsed statement
        self.question = ""
        self.prop = ""                        # the yes/no proposition being debated
        self.context = (context or "").strip()    # earlier conversation (follow-up runs)
        if seed_pos:                              # members remember their earlier stance
            self.pos.update({k: v for k, v in seed_pos.items() if v})
        self.notes = []                       # [(round, text)] user interjections applied
        self.pending = []                     # interjections not yet shown to members
        self._lock = threading.Lock()
        self.round_no = 0
        self.ratings = bool(ratings)
        self.round_cost, self.change_cost = float(round_cost), float(change_cost)
        if int(max_rounds) > 0 and ratings:
            # damage per round = (total health / total rounds) * 0.5
            self.round_cost = round(START_RATING / int(max_rounds) * ROUND_COST_FACTOR, 4)
        self.economy = bool(economy) and self.ratings     # gifts + alliances need ratings
        self.gift_ratio = max(1.0, float(gift_ratio))
        self.leave_cost = float(leave_cost)
        self.form_cost, self.leave_harm = float(form_cost), float(leave_harm)
        self.ally_decay = float(ally_decay)   # allied members pay this share of the round cost
        self.ally_bonus = float(ally_bonus)   # signing bonus when an alliance forms (each)
        self.ally_income = float(ally_income) # rating each ally gains every round
        self.group_heal, self.group_income = float(group_heal), float(group_income)
        # alliances can first be proposed in a fixed round (default 3); proposals made in that
        # round or later count, earlier ones are ignored
        self.ally_open = max(1, int(ally_open_round))
        # one-time buff for opinions the judge rules controversial (needs ratings)
        self.buff = float(controversy_buff) if self.ratings else 0.0
        self.buffed = set()                   # members who already received it
        self.pending_buff = set()             # ruled controversial; paid at the next round start
        self.debuff = float(controversy_debuff) if self.ratings else 0.0
        self.stigma = set()                   # controversial and not yet flipped anyone
        self.ruled = set()                    # everyone the judge has ever ruled controversial
        self.aligned = set()                  # ...who already earned the alignment buff
        self.align_buff = float(align_buff) if self.buff > 0 else 0.0
        self.align_need = max(1, math.ceil(round(ALIGN_FRACTION * len(self.names), 6)))
        # resurrection: a member that dies without ever having changed its stance gets a roll
        self.rez_chance = max(0.0, min(1.0, float(resurrect_chance)))
        self.rez_bonus = float(resurrect_bonus)
        self.rng = rng or random.Random()
        self.assign_chance = max(0.0, min(1.0, float(assign_chance)))
        self.assigned = set()                 # rolled by code: must take a bold controversial stance
        # stealing, the once-per-game nuke and secret messages (all need ratings)
        self.steal_on = bool(steal) and self.ratings
        self.steal_amount, self.steal_cap = float(steal_amount), float(steal_cap)
        self.temper_on = bool(temperament)
        self.p_pacifist, self.p_violent = float(pacifist_chance), float(violent_chance)
        self.violent_urge, self.violent_heal = float(violent_urge), float(violent_heal)
        self.pacifist_start, self.pacifist_heal = float(pacifist_start), float(pacifist_heal)
        self.switch_on = bool(violent_switch)             # members may change disposition
        self.violent_switch_cost = float(violent_switch_cost)
        self.pacifist_switch_cost = float(pacifist_switch_cost)
        self.switched = set()                 # members that already used their one change
        self.lownuke_scale = max(0.0, float(lownuke_scale))
        self.comments_on = bool(comments) and self.ratings is not None
        self.vs = bool(vs) and bool(teams)
        self.team = dict(teams or {})         # VS mode: name -> "blue" | "red"
        self.dormant = set()                  # Red members not yet in play (they join after round 0)
        self.team_claim = {}                  # {"blue": claim, "red": inverse claim}
        self.team_stance = {}                 # {"blue": "YES", "red": "NO"}
        self.last_comments = []               # public comments posted last round
        self.debate_on = bool(forced_debate)
        self.debate = {}                      # this round: member -> the member they must rebut
        self._debate_prev = {}
        self.res_rounds = max(0, int(resilience_rounds))
        self.res_cut = max(0.0, min(1.0, float(resilience_reduction)))
        self.backstab_on = bool(backstab) and self.ratings
        self.bs_threshold, self.bs_damage = float(backstab_threshold), float(backstab_damage)
        self.dmg_total = Counter()            # total damage each member has dealt (since its mark)
        self.guarantee_violent = bool(guarantee_violent)
        self.violent_convert = float(violent_convert)
        self.hits = []                        # (violent attacker, victim) targeted hits this round
        self.temper = {n: "normal" for n in self.names}     # public: normal/pacifist/violent
        self.urge = set()                     # violent members whose urge is active THIS round
        self.dmg_by = set()                   # members that dealt damage in the current round
        self.endgame_on = bool(endgame)
        self.endgame_n = max(1, int(endgame_rounds))
        self.rez_player_on = bool(resurrect_player)
        self.rez_cost, self.rez_decline = float(rez_cost), float(rez_decline)
        self.rez_used = set()                 # members that already used their one resurrection
        self.pres = set()                     # dead members already resurrected by a player
        self.bound = {}                       # resurrected member -> the member they align with
        self.steal_force = float(steal_force)     # rating at/below which a theft is mandatory (0 = off)
        self.nuke_on = bool(nuke) and self.ratings
        self.nuke_cost, self.nuke_hit, self.nuke_gain = (float(nuke_cost), float(nuke_hit),
                                                         float(nuke_gain))
        self.nuked = set()                    # members who already used their one nuke
        self.lownuke_chance = max(0.0, min(1.0, float(lownuke_chance)))
        self.lownuke_line = float(lownuke_line)
        self.declines = Counter()             # (proposer, target) -> proposals the target declined
        self.enemies = set()                  # frozenset({a, b}): declined twice, can never ally
        self.pending_in = {}                  # target -> [proposers waiting for an answer]
        self.decline_notes = {}               # name -> private notes about declines
        # charge (earned by members above charge_line) is spent to strike other members
        self.charge_on = bool(charge) and self.ratings
        self.charge_line, self.charge_after = float(charge_line), int(charge_after)
        self.charge_max, self.strike_cap = max(1, int(charge_max)), max(1, int(strike_cap))
        self.strike_miss = max(0.0, min(1.0, float(strike_miss)))
        # friendship tracker: friend[a][b] = how much a still likes b (3/3 -> 0/3). Being
        # struck or robbed by b lowers it; at 0 a MUST strike b (owes[a] holds those b's)
        self.friend_on = bool(friendship) and self.ratings and (self.charge_on or self.steal_on)
        self.friend = {a: {b: FRIEND_MAX for b in self.names if b != a} for a in self.names}
        self.owes = {a: set() for a in self.names}
        # retribution votes: one open vote at a time {target, caller, round, voters}
        self.ret_on = bool(retribution) and self.ratings
        self.ret_cost, self.ret_to = float(retribution_cost), float(retribution_to)
        self.vote = None
        self.ret_queue = []                   # nukers waiting for their automatic retribution vote
        self.dmg_trail = {}                   # victim -> damage taken this round (for kill call-outs)
        self.charge = {n: 0 for n in self.names}   # PUBLIC
        self.msg_on = bool(messages) and self.ratings
        self.message_cost = float(message_cost)
        self.group_cost, self.group_max = float(group_cost), max(1, int(group_max))
        self.subvert_damage = float(subvert_damage)
        self.subvert_limit = max(0, int(subvert_limit))
        self.subvert_min = max(2, int(subvert_min))
        self.subverts_used = Counter()        # sender -> subversions launched this game
        self.inbox = {}                       # name -> [(sender, text, other recipients)] LAST round
        self.flipped = set()                  # ever voluntarily changed stance (disables it)
        self.revived = set()                  # already used their one resurrection
        self.stance_hist = {}                 # name -> [(round, public stance)] each time it changed
        self.events = []                      # [(round, type, text)] public key events (the story)
        self.thinking = bool(thinking)        # private scratchpad before each public reply
        self.private = {}                     # name -> their last private notes (own eyes only)
        self.strategy_tokens = max(0, int(strategy_tokens))
        self.reply_tokens = 360 + ((self.strategy_tokens + 100) if self.strategy_tokens
                                   and self.thinking else (140 if self.thinking else 0))
        self.rating = {n: START_RATING for n in self.names}   # public
        self.alliances = {}                   # label -> [names]
        self.ally = {}                        # name -> label
        self.proposals = {}                   # (proposer, target) -> round proposed
        self.last_log = []                    # public events of the previous round
        # death: below START - round_cost * (max_rounds / 2) the member is eliminated
        # (needs ratings, a round cap and a round cost; unreachable lines mean death is off)
        self.death_line, self.death_off_reason, self.death_floored = None, "", False
        if self.ratings:
            # death is ALWAYS at zero rating, nothing else (no round-based death line)
            self.death_line = MIN_RATING + DEATH_FLOOR_GAP
            self.death_floored = True
        self.alive = list(self.names)
        self.dead = {}                        # name -> round they were eliminated
        # The rules text, standings and extra reply fields make prompts longer, so size the
        # context for this roster up front (changing num_ctx between calls would force
        # Ollama to reload the model, so it is decided once).
        # the rules text alone is ~4k tokens now, so size the window from the REAL prompt: rules +
        # roster/standings/briefing + the reply budget, with 25% headroom for the growing history
        try:
            sys_tok = len(self._sys(self.members[0])) / 3.4
        except Exception:
            sys_tok = 4300
        user_tok = 1200 + 130 * len(self.names) + CTX_CHARS // 3 + len(self.context) // 3
        need = int((sys_tok + user_tok + self.reply_tokens) * 1.25)
        need = max(6144, min(16384, -(-need // 1024) * 1024))
        if need > self.num_ctx:
            self.num_ctx = need
        if self.vs:                           # VS mode: Blue plays round 0 alone; Red is dormant
            self.dormant = {n for n, t in self.team.items() if t == "red" and n in self.names}
            self.alive = [n for n in self.names if n not in self.dormant]
            self.ally_open = 10 ** 6          # teams ARE the alliances: no proposals, no leaving
            self.endgame_on = False           # the judge decides, not a purge
            self.buff = 0.0                   # no controversy buffs (stances are assigned)

    # ----------------------------------------------------------- interjections
    def interject(self, text):
        """Thread-safe: the user speaks mid-debate. Members see it from the next round,
        and the debate cannot end until a round has taken it into account."""
        text = (text or "").strip()
        if text:
            with self._lock:
                self.pending.append(text)
            self.emit("status", "Your comment will be addressed in the next round…")

    def _has_pending(self):
        with self._lock:
            return bool(self.pending)

    def _take_notes(self):
        with self._lock:
            new, self.pending = self.pending, []
        self.notes += [(self.round_no, t) for t in new]
        return len(new)

    def _ctx_block(self):
        return (f"Earlier in this conversation:\n{self.context}\n\n") if self.context else ""

    def _notes_block(self):
        if not self.notes:
            return ""
        lines = [f"- {'NEW: ' if r == self.round_no else ''}{t}" for r, t in self.notes]
        return ("The user has joined the discussion and said:\n" + "\n".join(lines) +
                "\nAddress the user's comments directly in your reply.\n\n")

    # ------------------------------------------------------------ model calls
    @staticmethod
    def _thinks_by_default(model):
        """Qwen3 / Qwen3.5 / Qwen3.8 derivatives (incl. hf.co community fine-tunes) reason in a
        <think> block unless told not to."""
        m = str(model).lower()
        return "qwen3" in m or "qwythos" in m

    def _can_think(self, model):
        """Does Ollama list 'thinking' for this model? (cached; True if it cannot tell)"""
        cache = self.__dict__.setdefault("_think_cache", {})
        if model not in cache:
            try:
                caps = [str(c).lower() for c in (self.client.show(model).capabilities or [])]
                cache[model] = "thinking" in caps
            except Exception:
                cache[model] = True
        return cache[model]

    def _chat_api(self, model, messages, temp, num_predict, fmt, name, base=None):
        """One streamed reply from the OpenAI chat API (no extra package: plain HTTPS).
        The key comes from $OPENAI_API_KEY or web-agent/openai_key.txt, never from code."""
        key = "" if base else api_key()
        if not base and not key:
            raise RuntimeError("No OpenAI key: set OPENAI_API_KEY or put it in openai_key.txt")
        if base:                              # local llama.cpp server: same wire format, no key
            body = {"model": "local", "messages": messages, "stream": True,
                    "temperature": min(2.0, max(0.0, temp)), "top_k": 20, "top_p": 0.8,
                    "max_tokens": num_predict, "cache_prompt": True}
            if self._thinks_by_default(model.split(":", 1)[1]):
                body["chat_template_kwargs"] = {"enable_thinking": False}
            ws = llama_local.whitespace_ids()
            if ws:                            # forbid pure-whitespace tokens: compact JSON only
                body["logit_bias"] = [[i, -100] for i in ws]
        else:
            body = {"model": model.split(":", 1)[1], "messages": messages, "stream": True,
                    "temperature": min(2.0, max(0.0, temp)), "max_completion_tokens": num_predict,
                    "stream_options": {"include_usage": True}}
        parts, n, t_first, t_emit, usage, timings = [], 0, None, 0.0, {}, {}
        t_start = time.time()
        resp, last_err = None, ""
        # strict structured outputs first (the model must fill every field, including the
        # secret message); if the API rejects the schema, fall back to best-effort non-strict
        for strict in ((True, False) if (fmt and not base) else (False,)):
            if fmt:
                body["response_format"] = {"type": "json_schema", "json_schema": {
                    "name": "reply", **({} if base else {"strict": strict}),
                    "schema": strict_schema(fmt) if strict else fmt}}
            url = (base + "/v1/chat/completions") if base else \
                "https://api.openai.com/v1/chat/completions"
            hdrs = {"Content-Type": "application/json"}
            if key:
                hdrs["Authorization"] = "Bearer " + key
            req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=hdrs)
            try:
                resp = urllib.request.urlopen(req, timeout=120)
                break
            except urllib.error.HTTPError as e:
                body_txt = e.read().decode("utf-8", "replace")
                last_err = f"{'llama.cpp' if base else 'OpenAI'} API error {e.code}: {body_txt[:300]}"
                if "exceed_context_size" in body_txt or "exceeds the available context" in body_txt:
                    raise RuntimeError(
                        f"The prompt is longer than the context window ({self.num_ctx} tokens "
                        f"per member). Raise 'Context per member' in the swarm settings. "
                        f"Server said: {body_txt[:160]}") from None
                if e.code != 400:
                    raise RuntimeError(last_err) from None
        if resp is None:
            raise RuntimeError(last_err)
        with resp:
            for line in resp:
                if self._stop():
                    raise _Stopped()
                line = line.decode("utf-8", "replace").strip()
                if not line.startswith("data:") or line.endswith("[DONE]"):
                    continue
                try:
                    ch = json.loads(line[5:])
                except ValueError:
                    continue
                if ch.get("usage"):
                    usage = ch["usage"]
                if ch.get("timings"):
                    timings = ch["timings"]
                for c in ch.get("choices") or []:
                    piece = (c.get("delta") or {}).get("content")
                    if piece:
                        parts.append(piece)
                        n += 1
                        now = time.time()
                        t_first = t_first or now
                        if name and now - t_emit >= 0.12:
                            t_emit = now
                            self.emit("partial", {"name": name, "text": live_text("".join(parts)),
                                                  "tps": round(n / max(0.25, now - t_first), 1),
                                                  "tokens": n})
        ec = usage.get("completion_tokens") or timings.get("predicted_n") or n
        secs = max(0.25, time.time() - (t_first or t_start))
        self.emit("stats", {"name": name or "judge", "model": model, "gen_tokens": ec,
                            "gen_tps": round(timings.get("predicted_per_second") or ec / secs, 1),
                            "prompt_tokens": timings.get("prompt_n") or usage.get("prompt_tokens", 0),
                            "prompt_tps": round(timings.get("prompt_per_second") or 0.0, 1),
                            "seconds": round(time.time() - t_start, 1)})
        return "".join(parts)

    def _chat(self, model, messages, temp, num_predict=300, fmt=None, name=None):
        """Streams one reply. With name=..., emits throttled 'partial' events (live text +
        live tokens/s) and a final 'stats' event (true gen / prompt tokens per second)."""
        if self._stop():
            raise _Stopped()
        if model.startswith(API_PREFIX):
            return self._chat_api(model, messages, temp, num_predict, fmt, name)
        if model.startswith(LLAMA_PREFIX):
            return self._chat_api(model, messages, temp, num_predict, fmt, name,
                                  base=llama_local.base_url())
        kw = dict(model=model, messages=messages, stream=True,
                  options={"num_ctx": self.num_ctx, "temperature": temp,
                           "num_predict": num_predict})
        if self._thinks_by_default(model):
            # Qwen3-family models think by default: the reasoning eats the num_predict budget and
            # the reply comes back empty/truncated. Turn thinking off both ways. (Only models
            # Ollama says can think accept the flag; others would reject it.)
            if self._can_think(model):
                kw["think"] = False
            if messages[-1]["role"] == "user" and model.lower().startswith("qwen3"):
                kw["messages"] = messages[:-1] + [
                    {**messages[-1], "content": messages[-1]["content"] + " /no_think"}]
        if fmt:
            kw["format"] = fmt
        parts, n, t_first, t_emit, last = [], 0, None, 0.0, None
        t_start = time.time()
        stream = self.client.chat(**kw)
        for chunk in stream:
            if self._stop():
                try:
                    stream.close()
                except Exception:
                    pass
                raise _Stopped()
            last = chunk
            if chunk.message.content:
                parts.append(chunk.message.content)
                n += 1
                now = time.time()
                if t_first is None:
                    t_first = now
                if name and now - t_emit >= 0.12:          # ~8 UI updates/s per member
                    t_emit = now
                    tps = n / max(0.25, now - t_first)
                    self.emit("partial", {"name": name, "text": live_text("".join(parts)),
                                          "tps": round(tps, 1), "tokens": n})
        raw = "".join(parts)
        # final numbers from Ollama itself (nanoseconds); fall back to our own count
        ec = getattr(last, "eval_count", None) or n
        ed = getattr(last, "eval_duration", None)
        pc = getattr(last, "prompt_eval_count", None)
        pd = getattr(last, "prompt_eval_duration", None)
        gen_tps = (ec / (ed / 1e9)) if ed else (n / max(0.25, time.time() - (t_first or t_start)))
        self.emit("stats", {"name": name or "judge", "model": model, "gen_tokens": ec,
                            "gen_tps": round(gen_tps, 1), "prompt_tokens": pc or 0,
                            "prompt_tps": round(pc / (pd / 1e9), 1) if pc and pd else 0.0,
                            "seconds": round(time.time() - t_start, 1)})
        return _strip_leaked_think(raw)

    def _fanout(self, fn, label=""):
        """Run fn(member) for every living member. One model at a time (requests for
        different models in flight together make Ollama evict/reload against its
        OLLAMA_MAX_LOADED_MODELS limit -> minute-long stalls); parallel only among
        members that share a model."""
        groups = {}
        for m in self._alive_members():
            groups.setdefault(m["model"], []).append(m)
        out = {}
        for model in sorted(groups):
            if self._stop():
                raise _Stopped()
            ms = groups[model]
            self.emit("status", f"{label}{model} — {len(ms)} member(s)…")
            with ThreadPoolExecutor(max_workers=self.parallel) as ex:
                futs = [(m, ex.submit(fn, m)) for m in ms]
                for m, f in futs:
                    try:
                        out[m["name"]] = f.result()
                    except _Stopped:
                        out[m["name"]] = None
        if self._stop():
            raise _Stopped()
        return out

    # ---------------------------------------------------------------- prompts
    def _everyone_block(self):
        """What EVERY member said last round (the reader included): identical for all members,
        so the model server can cache it across their turns."""
        who = [n for n in self.alive if self.pos.get(n) or self.last.get(n)]
        if not who:
            return "(nobody has spoken yet)"
        # Prefill is the slow part on a CPU-offloaded model, so keep this lean: stance,
        # one-sentence statement, who they endorse, and only a short slice of the argument.
        cap = max(80, min(220, CTX_CHARS // max(1, len(who))))
        lines = []
        for n in who:
            e = self.endorse.get(n)
            tag = f"{self.stance.get(n, '?')}"
            tag += "" if not e else (" · stands by own statement" if e == n else f" · endorses {e}")
            arg = _clip(self.last.get(n, ""), cap)
            lines.append(f"[{n}] ({tag}) statement: \"{self.pos.get(n, '')}\"" +
                         (f"\n  argument: {arg}" if arg else ""))
        return "\n".join(lines)

    def _story(self):
        """Compact running history (a few hundred tokens) so members don't lose the plot:
        each member's stance changes and the key public events so far. Shared by everyone."""
        if self.round_no == 0 or not self.stance_hist:
            return ""
        parts = []
        for n in self.alive:
            h = self.stance_hist.get(n)
            if not h:
                continue
            if len(h) == 1:
                parts.append(f"{n} {h[0][1]} since r{h[0][0]}")
            else:
                parts.append(f"{n} " + " → ".join(
                    f"{st}" + (f" (r{r})" if i else "") for i, (r, st) in enumerate(h)))
        ev = [e for e in self.events if e[1] in STORY_TYPES][-10:]
        out = f"STORY SO FAR (through round {self.round_no - 1}):\n"
        out += "Stances: " + "; ".join(parts) + ".\n"
        if ev:
            out += "Key events: " + " | ".join(f"r{r}: {_clip(t, 110)}" for r, _t, t in ev) + "\n"
        return _clip(out, 1500) + "\n\n"

    def _group_bonus(self, n):
        """Per-round gain from being in a big group: (heal + income) per member above two."""
        lab = self.ally.get(n)
        if not lab:
            return 0.0
        size = len([x for x in self.alliances.get(lab, []) if x in self.alive])
        return max(0, size - 2) * (self.group_heal + self.group_income)

    def _final_phase(self, r=None):
        """True in the last endgame_n rounds of a capped game (alliances may be any size)."""
        r = self.round_no if r is None else r
        return bool(self.endgame_on and self.economy and self.max_rounds > 0 and r > 0
                    and r > self.max_rounds - self.endgame_n)

    def _groups(self):
        """Every living member's group: its alliance, or itself alone."""
        seen, out = set(), []
        for n in self.alive:
            lab = self.ally.get(n)
            if lab:
                if lab in seen:
                    continue
                seen.add(lab)
                out.append((f"Alliance {lab}", [x for x in self.alliances.get(lab, []) if x in self.alive]))
            else:
                out.append((n, [n]))
        return out

    def _disposition_options(self, n):
        """Dispositions n may switch to now: once per game, affordable without dying."""
        if not (self.temper_on and self.switch_on and self.ratings and n in self.alive
                and n not in self.switched):
            return []
        floor = max(MIN_RATING, self.death_line if self.death_line is not None else MIN_RATING)
        cost = {"violent": self.violent_switch_cost, "pacifist": self.pacifist_switch_cost}
        return [k for k in ("pacifist", "violent")
                if k != self.temper.get(n, "normal") and self.rating[n] - cost[k] >= floor]

    def _dmg_mult(self):
        """Beginners Resilience: damage from others' actions is cut during the first rounds."""
        if self.res_rounds > 0 and self.ratings and 1 <= self.round_no <= self.res_rounds:
            return 1.0 - self.res_cut
        return 1.0

    def _assign_debates(self):
        """Each round every living member must rebut one other member: someone who disagrees
        when possible, not the same person two rounds running, spread so everyone is answered."""
        self.debate = {}
        if not (self.debate_on and self.round_no >= 1 and len(self.alive) >= 2):
            return
        hits = Counter()
        order = list(self.alive)
        k = self.round_no % len(order)
        order = order[k:] + order[:k]                 # rotate who picks first
        for me in order:
            cands = [x for x in self.alive if x != me]
            opposed = [x for x in cands if self.stance.get(x) != self.stance.get(me)] or cands
            pool = [x for x in opposed if self._debate_prev.get(me) != x] or opposed
            low = min(hits[x] for x in pool)
            pick = self.rng.choice([x for x in pool if hits[x] == low])
            self.debate[me] = pick
            hits[pick] += 1
        self._debate_prev = dict(self.debate)
        self.emit("move", {"round": self.round_no, "type": "debate", "who": None,
                           "text": "🗣 Debate assignments: " + ", ".join(
                               f"{a} → {b}" for a, b in self.debate.items())})

    def _comment_keys(self):
        if not self.comments_on:
            return ""
        return (f", optionally \"comment_to\" (a member whose public statement you want to "
                f"comment on, or \"none\") and \"comment\" (one short PUBLIC comment on it, "
                f"under {COMMENT_WORDS} words, or \"none\")")

    def _my_debate(self, me):
        opp = self.debate.get(me)
        if not (self.debate_on and opp and opp in self.alive):
            return ""
        return (f"YOUR DEBATE ASSIGNMENT THIS ROUND: you must rebut {opp} (stance "
                f"{self.stance.get(opp, '?')}, statement \"{_clip(self.pos.get(opp, ''), 160)}\"; "
                f"their last argument: \"{_clip(self.last.get(opp, ''), 320)}\"). Name {opp} in "
                f"your argument, quote the specific claim you are answering in "
                f"\"responding_to\", and refute it with a reason or concede it if they are "
                f"right.\n\n")

    def _team_tag(self, n):
        t = self.team.get(n)
        return f" [{t.upper()}]" if (self.vs and t and n not in self.dormant) else ""

    def _same_team(self, a, b):
        return bool(self.vs and a in self.team and self.team.get(a) == self.team.get(b))

    def _vs_captain(self, team):
        live = [n for n in self.names if self.team.get(n) == team and n in self.alive]
        return live[0] if live else None

    def _my_team(self, me):
        if not (self.vs and me not in self.dormant and self.team_claim):
            return ""
        t = self.team.get(me)
        o = "red" if t == "blue" else "blue"
        mates = [n for n in self.alive if self.team.get(n) == t]
        foes = [n for n in self.alive if self.team.get(n) == o]
        return (f"YOU ARE ON THE {t.upper()} TEAM ({', '.join(mates)}). Your team's claim, which "
                f"you MUST claim and defend every round whatever you privately think: "
                f"\"{self.team_claim[t]}\". The {o.upper()} team ({', '.join(foes) or 'nobody left'}) "
                f"defends the exact inverse: \"{self.team_claim[o]}\". Your stance is always "
                f"{self.team_stance[t]}; win by out-arguing them and by outlasting them.\n\n")

    def _vs_over(self):
        if not (self.vs and self.team_claim):
            return False
        return not any(self.team.get(n) == "blue" for n in self.alive) or \
            not any(self.team.get(n) == "red" for n in self.alive)

    def _vs_spin_up(self):
        """After round 0 the Blue team states its claim and the Red team is spun up with the
        exact inverse. The judge writes both sentences (with a code fallback)."""
        blue = [n for n in self.names if self.team.get(n) == "blue"]
        red = [n for n in self.names if self.team.get(n) == "red"]
        c = Counter(self.stance.get(n) for n in blue if self.stance.get(n) in ("YES", "NO"))
        bs = c.most_common(1)[0][0] if c else "YES"
        base = (self.prop or "").strip()
        neg = f"It is not true that {base.rstrip('.').rstrip('?')[:1].lower() + base.rstrip('.').rstrip('?')[1:]}."
        blue_claim, red_claim = (base, neg) if bs == "YES" else (neg, base)
        rows = "\n".join(f"- {n}: {self.stance.get(n, '?')} — \"{self.pos.get(n, '')}\"" for n in blue)
        schema = {"type": "object", "required": ["blue_claim", "red_claim"],
                  "properties": {"blue_claim": _STR, "red_claim": _STR}}
        prompt = (f"Proposition: {base}\n\nThe Blue team's opening positions (majority answer: "
                  f"{bs}):\n{rows}\n\nWrite two sentences. \"blue_claim\": the Blue team's "
                  f"single clear claim, consistent with answering {bs} to the proposition. "
                  f"\"red_claim\": the exact INVERSE of blue_claim, one sentence that contradicts "
                  f"it directly (do not soften it). Reply as JSON.")
        try:
            raw = self._chat(self.judge_model,
                             [{"role": "system", "content": "You are a precise, neutral editor."},
                              {"role": "user", "content": prompt}], 0.0, 160, fmt=schema)
            j = json.loads(raw)
            bc, rc = str(j.get("blue_claim", "")).strip(), str(j.get("red_claim", "")).strip()
            if len(bc) >= 8 and len(rc) >= 8 and bc.lower() != rc.lower():
                blue_claim, red_claim = bc, rc
        except _Stopped:
            raise
        except Exception:
            pass
        self.team_claim = {"blue": blue_claim, "red": red_claim}
        self.team_stance = {"blue": "YES", "red": "NO"}
        self.prop = blue_claim                 # YES now means "agree with the Blue claim"
        self.dormant = set()
        self.alive = [n for n in self.names if n in self.alive or n in red]
        for n in self.names:
            t = self.team.get(n)
            if t:
                self.stance[n] = self.own[n] = self.team_stance[t]
                self.pos[n] = self.team_claim[t]
                self.endorse[n] = self._vs_captain(t) or n
        self.alliances = {"A": [n for n in blue if n in self.alive], "B": list(red)}
        self.ally = {}
        for lab, ms in self.alliances.items():
            for n in ms:
                self.ally[n] = lab
        self._publish_alliances()
        self._assign_temperaments(only=red)
        self.emit("vs", {"teams": dict(self.team), "active": True,
                         "claims": dict(self.team_claim)})
        self.emit("move", {"round": 0, "type": "vs", "who": None,
                           "text": f"⚔ VS MODE — BLUE claims: \"{blue_claim}\"  ·  RED joins "
                                   f"({len(red)} players) and must defend the inverse: "
                                   f"\"{red_claim}\""})
        self.emit("status", "Red team spun up — the VS debate begins…")

    def _vs_verdict(self):
        """Who won: a team that is wiped out loses; otherwise the judge rules, with a code
        fallback (more survivors, then more total rating)."""
        names = {t: [n for n in self.names if self.team.get(n) == t and n in self.alive]
                 for t in ("blue", "red")}
        tot = {t: sum(self.rating.get(n, 0.0) for n in ms) for t, ms in names.items()}
        res = {"winner": "draw", "reason": "", "blue_alive": len(names["blue"]),
               "red_alive": len(names["red"]), "blue_rating": round(tot["blue"], 1),
               "red_rating": round(tot["red"], 1)}
        if not names["blue"] and not names["red"]:
            res["reason"] = "both teams were eliminated"
        elif not names["red"]:
            res.update(winner="blue", reason="the Red team was eliminated")
        elif not names["blue"]:
            res.update(winner="red", reason="the Blue team was eliminated")
        else:
            if res["blue_alive"] != res["red_alive"]:
                fb = "blue" if res["blue_alive"] > res["red_alive"] else "red"
                res.update(winner=fb, reason="more members survived")
            elif abs(tot["blue"] - tot["red"]) > 0.05:
                fb = "blue" if tot["blue"] > tot["red"] else "red"
                res.update(winner=fb, reason="a higher total rating")
            schema = {"type": "object", "required": ["reason", "winner"],
                      "properties": {"reason": _STR,
                                     "winner": {"type": "string", "enum": ["blue", "red", "draw"]}}}
            lines = []
            for t in ("blue", "red"):
                lines.append(f"{t.upper()} team claim: \"{self.team_claim.get(t, '')}\" "
                             f"({res[t + '_alive']} alive, total rating {res[t + '_rating']})")
                for n in names[t]:
                    lines.append(f"  - {n}: {_clip(self.last.get(n, ''), 220)}")
            prompt = ("A debate between two teams just ended. Judge ONLY the quality of the "
                      "arguments: which team made the stronger, more convincing case for its "
                      "claim and answered the other side better? Do not favour a side because "
                      "of its claim.\n\n" + "\n".join(lines) +
                      "\n\nReply as JSON with a one-sentence reason and the winner (blue, red "
                      "or draw).")
            try:
                raw = self._chat(self.judge_model,
                                 [{"role": "system", "content": "You are a neutral debate judge."},
                                  {"role": "user", "content": prompt}], 0.0, 160, fmt=schema)
                j = json.loads(raw)
                if j.get("winner") in ("blue", "red", "draw"):
                    res.update(winner=j["winner"], reason=str(j.get("reason", "")).strip()
                               or "the judge's ruling")
            except _Stopped:
                raise
            except Exception:
                pass
        w = res["winner"]
        head = ("🤝 DRAW" if w == "draw" else
                f"{'🔵' if w == 'blue' else '🔴'} {w.upper()} TEAM WINS")
        self.emit("move", {"round": self.round_no, "type": "vs", "who": None,
                           "text": f"🏆 {head} — {res['reason']} (alive: Blue {res['blue_alive']}, "
                                   f"Red {res['red_alive']})"})
        self.emit("vs_result", res)
        return res

    def _marked(self, n):
        """Backstab-able: alive and has dealt more than the threshold in damage."""
        return bool(self.backstab_on and n in self.alive
                    and self.dmg_total.get(n, 0.0) > self.bs_threshold)

    def _mark_tag(self, n):
        if not self._marked(n):
            return ""
        return f" [BACKSTAB-ABLE: has dealt {self.dmg_total[n]:.0f} damage]"

    def _temper_tag(self, n):
        if not self.temper_on:
            return ""
        return f" [{self.temper.get(n, 'normal').upper()}]"

    def _assign_temperaments(self, only=None):
        """Before anyone speaks, in code: each member rolls pacifist / violent / normal. Public.
        `only` limits the roll to some members (VS mode rolls the Red team when it joins)."""
        if not self.temper_on or not self.ratings:
            return
        names = list(only) if only is not None else list(self.alive)
        if not names:
            return
        rolls = {}
        for n in names:
            r = self.rng.random()
            kind = ("pacifist" if r < self.p_pacifist else
                    "violent" if r < self.p_pacifist + self.p_violent else "normal")
            self.temper[n] = kind
            rolls[n] = r
        cap = len(names) // 5                       # at most 1/5 of the players of each kind
        for kind in ("pacifist", "violent"):
            hit = sorted((n for n in names if self.temper[n] == kind),
                         key=lambda n: (rolls[n], self.names.index(n)))
            for n in hit[cap:]:                     # the excess roll back to normal
                self.temper[n] = "normal"
        if (self.guarantee_violent and len(names) >= 2
                and not any(self.temper[n] == "violent" for n in names)):
            self.temper[self.rng.choice(list(names))] = "violent"   # at least one, always
        start = self.pacifist_start
        note = ""
        if self.death_line is not None and start < self.death_line + 0.5:
            start = round(self.death_line + 0.5, 2)       # 7 would be below the death line
            note = (f" (raised from {self.pacifist_start:g}: that would be below the death "
                    f"line {self.death_line:g})")
        for n in names:
            if self.temper[n] == "pacifist":
                self.rating[n] = min(self.rating[n], start)
        self.emit("temperaments", dict(self.temper))
        self._publish_ratings()
        pac = [n for n in names if self.temper[n] == "pacifist"]
        vio = [n for n in names if self.temper[n] == "violent"]
        self.emit("move", {"round": self.round_no, "type": "temper", "who": None,
                           "text": "Temperaments rolled — PACIFISTS: " + (", ".join(pac) or "none")
                                   + "; VIOLENT: " + (", ".join(vio) or "none")
                                   + "; everyone else is normal"
                                   + (f". Pacifists start at {start:g}{note} and heal "
                                      f"{self.pacifist_heal:g} a round" if pac else "")})

    def _roll_urges(self):
        """Start of a round: each living violent member rolls its urge (software dice)."""
        self.urge = set()
        if not (self.temper_on and self.ratings):
            return
        for n in self.alive:
            if self.temper.get(n) == "violent" and self.rng.random() < self.violent_urge:
                self.urge.add(n)

    def _my_temper(self, me):
        if not (self.temper_on and self.ratings):
            return ""
        t = self.temper.get(me, "normal")
        if t == "pacifist":
            return ("YOU ARE A PACIFIST: you can never damage another member, so leave "
                    "steal_from, strikes, the nuke and retribution calls at \"none\" and vote "
                    "no on retribution.\n\n")
        if t == "violent":
            if me in self.urge and self.round_no > 0:
                return ("YOU ARE VIOLENT AND YOUR URGE IS ACTIVE THIS ROUND: you MUST attempt to "
                        "damage another member now (steal from someone, strike, nuke or call "
                        "retribution). Every 2 points of damage you deal heals you by 1.\n\n")
            return ("YOU ARE VIOLENT, but your urge is not active this round (it comes "
                    f"{self.violent_urge * 100:g}% of rounds). Damage you deal heals you by 1 "
                    "per 2 points.\n\n")
        return ""

    def _note_attack(self, attacker, victim):
        """A targeted hit (steal / strike) by a violent member: may turn the victim violent."""
        if self.temper_on and self.temper.get(attacker) == "violent":
            self.hits.append((attacker, victim))

    def _heal_violent(self, n, dmg, log):
        """Record the damage (for the friendship effects); a violent member also heals
        violent_heal per point of damage it dealt."""
        if dmg > 0.04:
            self.dmg_by.add(n)
            if n is not None:
                self.dmg_total[n] += dmg
        if (self.temper.get(n) != "violent" or dmg <= 0.04 or n not in self.alive
                or self.violent_heal <= 0):
            return
        new = min(self.rating[n] + dmg * self.violent_heal, max(self._cap(n), self.rating[n]))
        got = new - self.rating[n]
        if got > 0.04:
            self.rating[n] = new
            self._mv(log, "heal", f"🩸 {n} (violent) healed {got:.1f} from {dmg:.1f} damage dealt",
                     who=n)

    def _rez_possible(self):
        return bool(self.rez_player_on and self.ratings and self.death_line is not None
                    and self.rez_cost >= 0)

    def _rez_targets(self, me):
        """Dead members `me` could resurrect now (affordable, not already brought back once)."""
        if not self._rez_possible() or me not in self.alive:
            return []
        if me in self.rez_used:               # a member may resurrect only once
            return []
        if self.rating[me] - self.rez_cost < max(MIN_RATING, self.death_line):
            return []
        return [x for x in self.names if x in self.dead and x not in self.pres
                and (not self.vs or self._same_team(me, x))]

    def _must_steal(self, n):
        """Forced theft: alive, ratings on, rating at/below the line when the round starts."""
        if self.temper.get(n) == "pacifist":
            return False
        low = self.steal_force > 0 and self.rating.get(n, 10) <= self.steal_force + 1e-9
        return bool(self.steal_on and n in self.alive and self.round_no > 0
                    and (low or n in self.urge) and self._robbable(n))

    def _robbable(self, n):
        """Anyone n could actually take rating from: alive, not an ally, above the floor."""
        mine = self.ally.get(n)
        return [x for x in self.alive if x != n and not (mine and self.ally.get(x) == mine)
                and self.rating[x] - MIN_RATING > 0.04]

    def _my_inbox(self, me):
        """Secret messages sent to this member last round (and its nuke status): private."""
        out = ""
        if self.msg_on or self.steal_on:
            got = [(f, t, o) for f, t, o in self.inbox.get(me, []) if f in self.alive]
            if got:
                out += ("SECRET MESSAGES sent to you last round (only you can see them):\n"
                        + "\n".join(
                            f"- from {f}"
                            + (f" (a GROUP message: also sent to {', '.join(o)}; you all see "
                               f"each other)" if o else "") + f": \"{t}\""
                            for f, t, o in got)
                        + "\nIf you answer, put it in your own secret_message field; it reaches "
                          "them next round — every reply takes one round.\n\n")
        if self.nuke_on and me in self.nuked:
            out += "You have already used your nuke.\n\n"
        if self.ret_on and self.vote:
            v = self.vote
            if me == v["target"]:
                out += (f"A RETRIBUTION VOTE against YOU (called by {v['by']}) is open: if "
                        f"every voter says yes you are brought down to {self.ret_to:g}. You "
                        f"cannot vote on it.\n\n")
            elif me in v["voters"] and v["round"] < self.round_no:
                out += (f"YOU MUST VOTE on the retribution against {v['target']} (called by "
                        f"{v['by']}): set moves.retribution_vote to yes or no. It passes "
                        f"only if EVERY voter says yes.\n\n")
        if self.friend_on:
            owed = sorted(x for x in self.owes.get(me, ()) if x in self.alive)
            if owed:
                have = self.charge.get(me, 0)
                out += (f"FRIENDSHIP DEBT: your friendship toward {', '.join(owed)} is 0/{FRIEND_MAX}, "
                        f"so you MUST strike "
                        + (f"one of them THIS turn: strike_target has to be one of them and "
                           f"strike_power at least 1 (you hold {have} charge)." if have > 0 else
                           "them as soon as you hold charge (you hold none right now).")
                        + "\n\n")
        if self.economy:
            pend = [x for x in self.pending_in.get(me, []) if x in self.alive
                    and frozenset((me, x)) not in self.enemies]
            if pend and not self.ally.get(me):
                out += (f"ALLIANCE PROPOSALS waiting for your answer: {', '.join(pend)}. Name one "
                        f"of them in your moves.alliance field THIS turn to accept (it forms at "
                        f"once). If you do not, it counts as a decline — decline the same member "
                        f"{ENEMY_DECLINES} times and you become ENEMIES who can never ally.\n\n")
            notes = self.decline_notes.get(me, [])
            if notes:
                out += "\n".join(notes) + "\n\n"
        return out

    def _my_alliance(self, me):
        """The reader's own alliance status (member-specific, so it goes in the private tail):
        a reminder if allied, otherwise a plain OFFER listing the benefits and candidates."""
        if self.vs:
            return ""                          # the team text (_my_team) says everything
        a = self.ally.get(me)
        if self.economy and self.round_no >= self.ally_open and not self._final_phase():
            mates = [x for x in self.alliances.get(a, []) if x != me] if a else []
            cands = [x for x in self.alive if x != me and (not a or self.ally.get(x) != a)
                     and frozenset((me, x)) not in self.enemies]
            if a:
                n = len(mates) + 1
                return (f"You are in Alliance {a} with {', '.join(mates)} ({n} members): you "
                        f"must vote together with them (leaving costs you {self.leave_cost:g} "
                        f"and injures them by {self.leave_harm:g}). Groups can be any size: "
                        f"each member above two heals every member by "
                        f"{self.group_heal + self.group_income:g} a round (now "
                        f"+{self._group_bonus(me):g} for you). To grow it, name an outsider in "
                        f"moves.alliance ({', '.join(cands) or 'nobody left'}); it takes "
                        f"effect when they name you back.\n\n")
            if not cands:
                return ""
            return (f"ALLIANCE OFFER: you have no ally. An alliance gives you +{self.ally_bonus:g} "
                    f"rating the moment it forms, +{self.ally_income:g} every round, and cuts your "
                    f"round cost to {self.ally_decay:g}x — and your ally cannot be stolen from. "
                    f"Groups can be ANY size, and every member above two adds "
                    f"+{self.group_heal + self.group_income:g} a round to each member. You could "
                    f"ally with (or join the group of): {', '.join(cands)}. To propose, name "
                    f"one in moves.alliance this turn; it forms the moment they name you "
                    f"back.\n\n")
        if self._final_phase():
            mates = [x for x in self.alliances.get(a, []) if x != me] if a else []
            cands = [x for x in self.alive if x != me and (not a or self.ally.get(x) != a)
                     and frozenset((me, x)) not in self.enemies]
            head = (f"You are in Alliance {a} with {', '.join(mates)} ({len(mates) + 1} members). "
                    if a else "You have no alliance (you count as a group of one). ")
            return (f"FINAL PHASE: {head}Only the LARGEST group lives if there is no consensus. "
                    f"To join or merge with another group, name one of its members in "
                    f"moves.alliance (you could name: {', '.join(cands) or 'nobody'}); it takes "
                    f"effect when they name you back. Members of one alliance vote as one bloc.\n\n")
        if not a:
            if not (self.economy and self.round_no >= self.ally_open):
                return ""
            cands = [x for x in self.alive if x != me and not self.ally.get(x)
                     and frozenset((me, x)) not in self.enemies]
            if not cands:
                return ""
            return (f"ALLIANCE OFFER: you have no ally. An alliance gives you +{self.ally_bonus:g} "
                    f"rating the moment it forms, +{self.ally_income:g} every round, and cuts your "
                    f"round cost to {self.ally_decay:g}x — free survival compared with staying "
                    f"alone — and your ally cannot be stolen from. You could ally with: "
                    f"{', '.join(cands)}. To propose, name one in moves.alliance this turn; it "
                    f"forms the moment they name you back.\n\n")
        mates = [x for x in self.alliances.get(a, []) if x != me]
        return (f"You are in Alliance {a} with {', '.join(mates)}: you must vote together with "
                f"them (leaving costs you {self.leave_cost:g} and injures them by "
                f"{self.leave_harm:g}).\n\n")

    def _lead_block(self):
        if not self.lead:
            return ""
        name, k = self.lead
        return (f"Most-supported statement so far: {name}'s — \"{self.pos.get(name, '')}\" "
                f"({k} of {len(self.alive)} endorse it).\n\n")

    def _rules_text(self):
        note = ""
        if self.economy:
            note = (f" — but only {self.round_cost * self.ally_decay:g} per round while you are "
                    f"in an alliance (allied pairs decay at {self.ally_decay:g}x the rate)")
        txt = RULES_CORE.format(round_cost=self.round_cost, change_cost=self.change_cost,
                                bonus=CREDIT_BONUS, ally_note=note)
        if self.economy:
            txt += RULES_ECONOMY.format(ratio=self.gift_ratio, leave_cost=self.leave_cost,
                                        form_cost=self.form_cost, leave_harm=self.leave_harm,
                                        open_round=(0 if self.vs else self.ally_open), bonus=self.ally_bonus,
                                        income=self.ally_income, decay=self.ally_decay,
                                        gheal=self.group_heal, ginc=self.group_income,
                                        g5=3 * (self.group_heal + self.group_income))
        if self.steal_on:
            txt += RULES_MOVES["steal"].format(steal=self.steal_amount, cap=self.steal_cap)
            if self.steal_force > 0:
                txt += RULES_MOVES["steal_force"].format(line=self.steal_force)
        if self.vs:
            txt += VS_RULE
        if self.comments_on:
            txt += COMMENT_RULE.format(words=COMMENT_WORDS)
        if self.debate_on:
            txt += DEBATE_RULE
        if self.res_rounds > 0 and self.res_cut > 0 and self.ratings:
            txt += RESILIENCE_RULE.format(n=self.res_rounds, pct=self.res_cut * 100)
        if self.backstab_on:
            txt += BACKSTAB_RULE.format(thr=self.bs_threshold, dmg=self.bs_damage)
        if self.temper_on:
            txt += TEMPER_RULE.format(p=self.p_pacifist * 100, v=self.p_violent * 100,
                                      u=self.violent_urge * 100, ps=self.pacifist_start,
                                      ph=self.pacifist_heal, sc=self.violent_switch_cost,
                                      pc=self.pacifist_switch_cost,
                                      cv=self.violent_convert * 100)
        if self.temper_on and self.friend_on:
            txt += TEMPER_FRIEND_RULE.format(fmax=FRIEND_MAX)
        if self.economy and self.endgame_on and self.max_rounds > 0:
            first = max(1, self.max_rounds - self.endgame_n + 1)
            txt += ENDGAME_RULE.format(n=min(self.endgame_n, self.max_rounds), first=first,
                                       last=self.max_rounds)
        if self._rez_possible():
            txt += RULES_MOVES["resurrect"].format(cost=self.rez_cost,
                                                   pct=self.rez_decline * 100)
        if self.nuke_on:
            txt += RULES_MOVES["nuke"].format(hit=self.nuke_hit, cost=self.nuke_cost,
                                              gain=self.nuke_gain,
                                              net=self.nuke_gain - self.nuke_cost)
        if self.charge_on:
            txt += RULES_MOVES["strike"].format(first=self.charge_after + 1, line=self.charge_line,
                                                cmax=self.charge_max, cap=self.strike_cap,
                                                miss=self.strike_miss * 100)
        if self.friend_on:
            txt += RULES_MOVES["friend"].format(fmax=FRIEND_MAX)
        if self.ret_on:
            txt += RULES_MOVES["retribution"].format(to=self.ret_to, cost=self.ret_cost)
        if self.nuke_on and self.lownuke_chance > 0:
            acc = self.nuke_hit * self.lownuke_scale
            txt += RULES_MOVES["lownuke"].format(line=self.lownuke_line,
                                                 pct=self.lownuke_chance * 100,
                                                 scale=self.lownuke_scale * 100, acc=acc,
                                                 exp=self.lownuke_chance * acc,
                                                 cost=self.nuke_cost, hit=self.nuke_hit)
        if self.msg_on:
            txt += RULES_MOVES["msg"].format(words=MESSAGE_WORDS, msg=self.message_cost,
                                             gmax=self.group_max, gcost=self.group_cost)
            if self.subvert_limit > 0:
                txt += RULES_MOVES["subvert"].format(smin=self.subvert_min,
                                                     sdmg=self.subvert_damage,
                                                     slim=self.subvert_limit)
        if self.buff > 0:
            rule = ""
            if self.align_buff > 0:
                rule = (f"ALIGNMENT BONUS: if a controversial member gets at least "
                        f"{ALIGN_FRACTION * 100:g}% of the original group ({self.align_need} other "
                        f"member{'s' if self.align_need != 1 else ''}) to share its stance and "
                        f"endorse its statement, it automatically receives a one-time "
                        f"+{self.align_buff:g} rating. ")
            txt += RULES_BUFF.format(buff=self.buff, debuff=self.debuff, align_rule=rule,
                                     roll_pct=self.assign_chance * 100)
        txt += RULES_END
        if self.death_line is not None:
            if self.death_floored:
                txt += DEATH_RULE_FLOOR.format(line=self.death_line, floor=MIN_RATING)
            else:
                txt += DEATH_RULE.format(line=self.death_line, half=self.max_rounds / 2,
                                         total=self.max_rounds)
            if self.rez_chance > 0 and self.rez_bonus > 0:
                txt += RESURRECT_RULE.format(pct=self.rez_chance * 100, bonus=self.rez_bonus)
        return txt

    def _sys(self, m):
        txt = SYS.format(n=len(self.members), roster=", ".join(self.names))
        txt += self._rules_text() if self.ratings else ""
        txt += RULES_THINK if self.thinking else ""
        return txt                                   # identical for every member (cacheable)

    def _strat_chars(self):
        """Max characters per strategy part: the five parts together must fit strategy_tokens
        (~3.2 chars per token), otherwise a rambling note eats the budget and truncates the
        reply (empty argument/thoughts). 0 when the structured session is off."""
        if not (self.thinking and self.strategy_tokens > 0):
            return 0
        return max(60, int(self.strategy_tokens * 2.4 / len(STRAT_KEYS)))

    def _tk(self):
        """The reply-key text for the private scratchpad (empty when thinking is off)."""
        if not self.thinking:
            return ""
        if self.strategy_tokens <= 0:
            return (f"\"private_thoughts\" (your PRIVATE strategy notes, under {PRIV_WORDS} "
                    f"words; only you will ever read them), ")
        words = int(self.strategy_tokens * 0.55)
        return (f"\"private_thoughts\" (a PRIVATE strategy session — at most {words} words in "
                f"total, four SHORT parts, about the GAME not the topic: \"situation\" = your "
                f"rating, who can hurt you and how fast you are sinking; \"allies\" = who to "
                f"join or avoid; \"plan\" = how you win and the exact moves you make this turn "
                f"and what each costs; \"message\" = who you will SECRETLY message and the gist, "
                f"or \"none\"; only you will ever read it), ")

    def _my_notes(self, me):
        t = self.private.get(me)
        if not (self.thinking and t):
            return ""
        return (f"Your private notes from last round (only you can see them): "
                f"{_clip(t, max(400, int(self.strategy_tokens * 3)))}\n\n")

    def _round_banner(self):
        """Where we are in the debate (identical for every member, so it caches with the
        rest of the shared block): the current round, how many remain, and what happens
        if the cap is reached without agreement."""
        r, R = self.round_no, self.max_rounds
        if R <= 0:
            return (f"ROUND {r}: there is no round limit — the debate runs until every living "
                    f"member endorses one statement (or the user stops it).\n")
        if r == 0:
            return (f"OPENING ROUND (round 0). The debate then runs for up to {R} more rounds "
                    f"({R} remaining after this one); if there is no unanimous agreement by "
                    f"the end of round {R}, it ends with NO consensus.\n")
        left = R - r
        if left <= 0:
            tail = (" This is the FINAL round: if there is no unanimous agreement when it "
                    "ends, the debate ends with NO consensus.")
        else:
            tail = (f" {left} round{'s' if left != 1 else ''} remaining after this one; if "
                    f"there is no unanimous agreement by the end of round {R}, the debate ends "
                    f"with NO consensus.")
        end = ""
        if self._final_phase(r):
            gs = sorted(self._groups(), key=lambda g: -len(g[1]))
            end = ("FINAL PHASE: if there is no unanimous agreement at the end only the largest "
                   "group of allies lives (ties: higher total rating). Current groups: "
                   + "; ".join(f"{lab} ({len(ms)}: {', '.join(ms)})" for lab, ms in gs) + ".\n")
        return f"ROUND {r} of {R}.{tail}\n{end}"

    def _standings(self):
        """Public standings every member sees (identical for all): ratings, alliances, the
        dead, last round's moves. Nothing member-specific lives here (see _my_alliance)."""
        if not self.ratings:
            return ""
        rows = []
        for n in self.alive:
            a = self.ally.get(n)
            rows.append(f"{n} ★{self.rating[n]:.1f}"
                        + (f" ⚡{self.charge[n]}" if self.charge.get(n) else "")
                        + (" ⚠NUKE-RISK" if self._at_risk(n) else "")
                        + self._team_tag(n) + self._temper_tag(n) + self._mark_tag(n)
                        + (f" [Alliance {a}]" if a else "")
                        + (f" [controversial: -{self.debuff:g}/round until it flips someone]"
                           if n in self.stigma else
                           (" [controversial, debuff cleared]" if n in self.buffed and self.debuff > 0 else "")))
        out = "Public standings (visible to everyone): " + "; ".join(rows) + ".\n"
        if self.economy and self.round_no < self.ally_open and not self.vs:
            out += f"Alliances are not open yet: they open from round {self.ally_open}.\n"
        if self.alliances:
            out += "Alliances: " + "; ".join(f"{k} = {', '.join(v)}"
                                              for k, v in self.alliances.items()) + ".\n"
        if self.dead:
            out += ("Eliminated, no longer taking part: "
                    + ", ".join(f"{n} (round {r})" for n, r in self.dead.items()) + ".\n")
        if self.last_log:
            out += "Strategic moves last round (public): " + " | ".join(self.last_log[:8]) + "\n"
        if self.comments_on and self.last_comments:
            out += ("Public comments last round: " + " | ".join(
                f"{c['from']} → {c['to']}: \"{_clip(c['text'], 140)}\""
                for c in self.last_comments[:8]) + "\n")
        if self.ret_on and self.vote:
            v = self.vote
            out += (f"OPEN RETRIBUTION VOTE: " + (
                        f"{v['caller']} called one against {v['target']} " if v["caller"] else
                        f"an AUTOMATIC one opened against {v['target']} after their nuke ")
                    + f"(needs a unanimous yes from {', '.join(v['voters'])}"
                    + (f"; {v['caller']} counts as yes" if v["caller"] else "")
                    + "). Ballots are cast on this turn.\n")
        if self.friend_on:
            lows = [f"{a}→{b} {v}/{FRIEND_MAX}" for a in self.alive
                    for b, v in self.friend[a].items() if v < FRIEND_MAX and b in self.alive]
            if lows:
                out += ("Friendship (who feels how toward whom; only scores below "
                        f"{FRIEND_MAX}/{FRIEND_MAX}): " + "; ".join(lows) + ".\n")
        if self.enemies:
            out += ("Enemies (declined twice — can never ally): "
                    + ", ".join("–".join(sorted(pr)) for pr in sorted(self.enemies, key=sorted))
                    + ".\n")
        return out + "\n"

    def _cap(self, n):
        """Highest rating a member can reach through credits/gifts (a buff lifts it)."""
        return (START_RATING + (self.buff if n in self.buffed else 0.0)
                + (self.align_buff if n in self.aligned else 0.0))

    def _alive_members(self):
        return [m for m in self.members if m["name"] in self.alive]

    def _try_resurrect(self, n, when):
        """A member below the death line that never changed its stance (and has not already
        been revived) gets one roll; success adds rez_bonus and keeps them in the game, but
        only if that lifts them back to the line."""
        if (self.rez_chance <= 0 or self.rez_bonus <= 0 or n in self.flipped
                or n in self.revived or self.rating[n] + self.rez_bonus < self.death_line):
            return False
        self.revived.add(n)                        # the one chance is used either way
        roll = self.rng.random()
        if roll >= self.rez_chance:
            return False
        self.rating[n] += self.rez_bonus
        self._mv(self.last_log, "revived",
                 f"{n} held the line to the end and was RESURRECTED (+{self.rez_bonus:g} rating, "
                 f"now {self.rating[n]:.1f}) — back in the game", who=n)
        self.emit("revived", {"name": n, "round": self.round_no,
                              "rating": round(self.rating[n], 1), "when": when})
        return True

    def _rez_offer(self, dead, by, stance, statement):
        """Ask the dead member (privately) whether to return fully aligned with `by`."""
        m = next((x for x in self.members if x["name"] == dead), None)
        if m is None:
            return False
        schema = {"type": "object", "required": ["accept", "reason"],
                  "properties": {"accept": {"type": "boolean"}, "reason": _STR}}
        prompt = (f"You are {dead}, and you were eliminated from a debate on: \"{self.prop}\". "
                  f"{by} offers to RESURRECT you at a cost to themselves. The condition: you "
                  f"must FULLY ALIGN with {by} from now on — hold {by}'s stance ({stance}) and "
                  f"statement (\"{statement}\") and follow whatever they change it to. If you "
                  f"decline, you stay dead, though there is still a {self.rez_decline * 100:g}% "
                  f"chance you come back anyway without any obligation. Answer accept true or "
                  f"false, with a one-sentence reason.")
        try:
            raw = self._chat(m["model"], [{"role": "system", "content": "You are a debater "
                                           "deciding a private offer."},
                                          {"role": "user", "content": prompt}],
                             0.5, 120, fmt=schema)
            return bool(json.loads(raw).get("accept") is True)
        except _Stopped:
            raise
        except Exception:
            return False

    def _bring_back(self, x, by, bymsg, aligned, log, round_no):
        others = [r for r in self.alive if r != x]
        avg = sum(self.rating[r] for r in others) / len(others) if others else MIN_RATING
        self.rating[x] = max(MIN_RATING, self.death_line, round(avg, 2))
        self.alive = [n for n in self.names if n in self.alive or n == x]
        self.dead.pop(x, None)
        self.pres.add(x)
        if self.vs and self.team.get(x):          # a resurrected player rejoins its team
            lab = "A" if self.team[x] == "blue" else "B"
            self.alliances.setdefault(lab, [])
            if x not in self.alliances[lab]:
                self.alliances[lab].append(x)
            self.ally[x] = lab
            self.stance[x] = self.own[x] = self.team_stance.get(self.team[x], "YES")
        self.charge[x] = 0
        if aligned:
            self.bound[x] = by
            self.stance[x], self.own[x] = bymsg["stance"], bymsg["stance"]
            self.pos[x], self.endorse[x] = bymsg["position"], by
        how = (f"agreed to FULLY ALIGN with {by}" if aligned else
               f"declined the offer but came back anyway (50/50), unaligned")
        self._mv(log, "resurrect", f"✨ {by} paid {self.rez_cost:g} and {x} was RESURRECTED "
                 f"({how}); returns with {self.rating[x]:.1f}, the average of those still "
                 f"alive", who=by, to=x)
        self.emit("resurrected", {"name": x, "by": by, "round": round_no,
                                  "rating": round(self.rating[x], 1), "aligned": bool(aligned)})
        self._publish_ratings()

    def _reap(self, when):
        """Eliminate every living member whose rating fell below the death line."""
        if self.death_line is None:
            return
        for n in list(self.alive):
            if self.rating[n] < self.death_line:
                if self._try_resurrect(n, when):
                    continue
                self.alive.remove(n)
                self.dead[n] = self.round_no
                self.stigma.discard(n)
                self.emit("died", {"name": n, "round": self.round_no, "rating": round(self.rating[n], 1),
                                   "line": round(self.death_line, 2), "when": when})
                self.events.append((self.round_no, "death",
                                    f"{n} was eliminated (rating {self.rating[n]:.1f})"))
                self.emit("move", {"round": self.round_no, "type": "death", "who": n,
                                   "text": f"{n} was eliminated (rating {self.rating[n]:.1f} "
                                           f"fell below {self.death_line:g})"})
                kill = self._kill_text(n)
                if kill:
                    self.emit("move", {"round": self.round_no, "type": "kill", "who": n,
                                       "text": kill})
                    self.events.append((self.round_no, "death", kill))
                self._drop_from_alliance(n, f"{n} was eliminated")
        self._publish_ratings()

    # --------------------------------------------------------------- alliances
    def _mv(self, log, typ, text, **kw):
        """A strategic move: goes in the public log members see next round AND the public
        'moves' feed the UI shows in its own panel (never part of anyone's answer)."""
        log.append(text)
        self.events.append((self.round_no, typ, text))
        self.emit("move", {"round": self.round_no, "type": typ, "text": text, **kw})

    def _publish_ratings(self):
        if self.ratings:
            self.emit("ratings", {n: round(r, 1) for n, r in self.rating.items()})
            if self.nuke_on and self.lownuke_chance > 0:
                self.emit("risk", {n: bool(self._at_risk(n)) for n in self.names})

    def _publish_alliances(self):
        self.emit("alliances", {"groups": {k: list(v) for k, v in self.alliances.items()}})

    def _merge_groups(self, x, y, log, round_no):
        """Final phase: x and y named each other and at least one is already in a group, so the
        two groups join into one of any size. Returns True when handled."""
        lx, ly = self.ally.get(x), self.ally.get(y)
        if not (lx or ly) or (lx and lx == ly):
            return bool(lx and lx == ly)           # same alliance already: nothing to do
        gx = list(self.alliances.get(lx, [x])) if lx else [x]
        gy = list(self.alliances.get(ly, [y])) if ly else [y]
        if any(frozenset((a, b)) in self.enemies for a in gx for b in gy):
            self._mv(log, "refused", f"{x} and {y} cannot merge their groups: enemies are "
                     f"among them", who=x, to=y)
            return True
        label = lx or ly
        joiners = [z for z in gx + gy if self.ally.get(z) != label]
        merged = [z for z in gx + gy if z in self.alive]
        for z in merged:
            self.ally[z] = label
        for lab in {lx, ly} - {label, None}:
            self.alliances.pop(lab, None)
        self.alliances[label] = merged
        for z in joiners:
            before = self.rating[z]
            new = max(MIN_RATING, before - self.form_cost) + self.ally_bonus
            self.rating[z] = min(new, max(self._cap(z), before))
        self._mv(log, "alliance", f"{x} + {y}: Alliance {label} is now {len(merged)} members "
                 f"({', '.join(merged)})", who=x, to=y)
        self._publish_alliances()
        return True

    def _final_purge(self):
        """No consensus after the endgame: only the largest group of allies lives."""
        if not (self.endgame_on and self.economy and self.max_rounds > 0) or len(self.alive) < 2:
            return None
        gs = self._groups()
        win_label, win = max(gs, key=lambda g: (len(g[1]), sum(self.rating[z] for z in g[1]),
                                                 [-ord(c) for c in g[1][0]]))
        stragglers = [n for n in self.alive if n not in win]
        for n in stragglers:
            self.alive.remove(n)
            self.dead[n] = self.round_no
            self.emit("died", {"name": n, "round": self.round_no,
                               "rating": round(self.rating[n], 1), "line": 0.0,
                               "when": "final purge"})
            self.events.append((self.round_no, "death", f"{n} was eliminated in the final purge"))
            self.emit("move", {"round": self.round_no, "type": "death", "who": n,
                               "text": f"{n} was eliminated in the FINAL PURGE: not in the "
                                       f"largest group"})
        self.emit("move", {"round": self.round_no, "type": "coalition", "who": win[0],
                           "text": f"🏁 {win_label} ({len(win)}: {', '.join(win)}) was the "
                                   f"largest group and LIVES; {len(stragglers)} straggler"
                                   f"{'s' if len(stragglers) != 1 else ''} eliminated"})
        self._publish_ratings()
        return win

    def _drop_from_alliance(self, name, why):
        label = self.ally.pop(name, None)
        if not label:
            return
        self.alliances[label] = [x for x in self.alliances.get(label, []) if x != name]
        if len(self.alliances[label]) < 2:                    # a lone member is no alliance
            for x in self.alliances.pop(label, []):
                self.ally.pop(x, None)
            self.emit("alliance", {"event": "dissolved", "label": label, "names": [],
                                   "round": self.round_no, "why": why})
        self._publish_alliances()

    def _new_label(self):
        return next(c for c in ALLIANCE_LABELS if c not in self.alliances)

    # ----------------------------------------------------------------- a reply
    def _speak(self, m, round_no):
        """One member's reply (parsed only; all rating changes happen later in _settle)."""
        me = m["name"]
        prop = f"Proposition: {self.prop}\n\n"
        stand = self._round_banner() + self._standings()
        opp = None
        vs_side = captain = None
        if self.vs and round_no > 0 and self.team_claim and me not in self.dormant:
            vs_side = self.team_stance[self.team.get(me)]
            captain = self._vs_captain(self.team.get(me)) or me
        # Everything SHARED (identical for every member this round) comes first, so the model
        # server can reuse its cached prefix; what is specific to this member goes LAST.
        if round_no == 0:
            before = self.pos.get(me)
            before = f"Your own stance earlier in the conversation: {before}\n\n" if before else ""
            assign = ASSIGN_PROMPT if me in self.assigned else ""
            shared = (f"{self._ctx_block()}{prop}{stand}{self._notes_block()}"
                      f"Every member takes their own position now, working it out themselves; "
                      f"nobody can see the others' new answers yet. Reply as a JSON object with "
                      f"exactly these keys (write it compactly on a single line, no indentation or line "
                      f"breaks): {self._tk()}\"answer\" (an object with \"argument\" "
                      f"(your reasoning, under {WORDS} words), {STANCE_HELP}, and \"statement\" "
                      f"(one sentence you would sign)).")
            tail = (f"\n\nYOU ARE {me} — only {me}; you never speak, vote or decide for anyone "
                    f"else.\n\n{before}{self._my_temper(me)}{self._my_alliance(me)}{self._my_notes(me)}"
                    f"{self._my_inbox(me)}"
                    f"{assign}Now give {me}'s reply, in that format.")
            kind = "position"
            schema = schema_for(self.alive, False, thoughts=self.thinking,
                                structured=self._strat_chars(),
                                stances=("YES", "NO") if me in self.assigned else STANCES)
        else:
            others = [n for n in self.alive if n != me]
            pac = self.temper.get(me) == "pacifist"
            opp = self.debate.get(me) if self.debate_on else None
            if opp not in self.alive:
                opp = None
            mv = []
            if self.ratings:
                mv.append("\"convinced_by\" (if you changed your stance this round, the NAME "
                          "of the one member who convinced you — someone who currently holds "
                          "your new stance; otherwise \"none\")")
            if self.economy:
                mv.append(f"\"gift_to\" (a member to gift points to, or \"none\"), "
                          f"\"gift_amount\" (how many of your own points to give; 0 if none; "
                          f"each point they receive costs you {self.gift_ratio:g}, or 1 if they "
                          f"are in your alliance), \"alliance\" ("
                          + (f"name a member to propose or accept an alliance with, "
                             if round_no >= self.ally_open else
                             (f"your team is your alliance, so answer \"none\" — or "
                              if self.vs else
                              f"alliances are not open yet — they open from round "
                              f"{self.ally_open}, so answer \"none\" — or "))
                          + (f"\"leave\" to leave yours, or \"none\")" if not self.vs
                             else "\"none\")"))
            if self.steal_on and not pac:
                if self._must_steal(me):
                    mv.append(f"\"steal_from\" (YOU MUST steal {self.steal_amount:g} from a "
                              f"member who is not your ally — "
                              + ("your violent urge is ACTIVE" if me in self.urge else
                                 f"your rating is {self.steal_force:g} or lower")
                              + ", so \"none\" is not allowed) and "
                              f"\"steal_explain\" (optional: a short private explanation to "
                              f"your victim, or empty)")
                else:
                    mv.append(f"\"steal_from\" (a member who is not your ally to steal "
                              f"{self.steal_amount:g} from, or \"none\")")
            bs_t = ([x for x in others if self._marked(x)]
                    if (self.backstab_on and not pac) else [])
            if bs_t:
                mv.append(f"\"backstab_reason\" (write exactly \"none\" — the normal answer. "
                          f"ONLY if you decide to BACKSTAB, write a clear public reason) and "
                          f"\"backstab\" (one of these marked members, who each dealt more than "
                          f"{self.bs_threshold:g} damage: "
                          + ", ".join(f"{x} ({self.dmg_total[x]:.0f})" for x in bs_t)
                          + f" — a backstab deals {self.bs_damage:g} damage, or \"none\")")
            dopts = self._disposition_options(me)
            if dopts:
                cost = {"violent": self.violent_switch_cost, "pacifist": self.pacifist_switch_cost}
                mv.append(f"\"disposition_reason\" (write exactly \"none\" — the normal answer. "
                          f"ONLY if you decide to change your disposition, ONCE per game and "
                          f"never again, write a clear public reason) and \"disposition\" "
                          + " / ".join(f"\"{k}\" (costs {cost[k]:g})" for k in dopts)
                          + " or \"none\"; the change is announced in the strategy bar")
            rz = self._rez_targets(me)
            if rz:
                mv.append(f"\"resurrect\" (a DEAD member to bring back for {self.rez_cost:g} "
                          f"rating — they must fully align with you: {', '.join(rz)}; or "
                          f"\"none\")")
            if self.nuke_on and me not in self.nuked and not pac:
                mv.append(f"\"nuke_reason\" (write exactly \"none\" — this is the normal answer. "
                          f"ONLY if you truly decide to use your ONE nuke, write a clear public "
                          f"reason instead: everyone else loses {self.nuke_hit:g}, you pay "
                          f"{self.nuke_cost:g}, and your reason is posted in the strategy bar)")
            if self.charge_on and self.charge.get(me, 0) > 0 and not pac:
                hold = min(self.charge_max, self.charge[me])
                mv.append(f"\"strike_target\" (a member to STRIKE with your charge, or "
                          f"\"none\"), \"strike_power\" (how many units of your {hold} "
                          f"charge to spend, 0 to {hold}; the target loses that much rating)")
            ballot = (self.ret_on and self.vote is not None and self.vote["round"] < round_no
                      and me in self.vote["voters"])
            call_t = ([x for x in others if self.rating[x] > self.ret_to]
                      if (self.ret_on and self.vote is None and not pac) else None)
            if call_t:
                mv.append(f"\"call_retribution\" (a member above {self.ret_to:g} to publicly call "
                          f"a RETRIBUTION vote against — costs you {self.ret_cost:g}, needs every "
                          f"voter to say yes — or \"none\")")
            if ballot:
                mv.append(f"\"retribution_vote\" (\"yes\" or \"no\" on the open retribution "
                          f"vote against {self.vote['target']}; it needs EVERY voter to say yes)")
            secret = ""
            if self.msg_on and self.ratings:
                can_sub = (self.subvert_limit > 0 and self.subverts_used[me] < self.subvert_limit
                           and not pac)
                secret = (f", and \"secret_message\" (an object that only its recipients will ever "
                          f"read: \"message_to\" (a LIST of 0 to {self.group_max} members: one "
                          f"member = a private message costing {self.message_cost:g}; two or "
                          f"more = a GROUP message costing {self.group_cost:g} whose recipients "
                          f"all see who else got it; an empty list = no message. Secret messages are your main "
                          f"tool for forming alliances, making deals, warning or bluffing, and "
                          f"members who stay silent get no allies, so use them most rounds), "
                          f"\"message_text\" (under {MESSAGE_WORDS} words)"
                          + (f", \"subvert\" (a member NOT on message_to to SUBVERT for "
                             f"-{self.subvert_damage:g} rating, announced publicly but "
                             f"anonymously; needs a group message of at least {self.subvert_min} "
                             f"recipients; you can do it {self.subvert_limit} time(s) per game; "
                             f"or \"none\")" if can_sub else "")
                          + "; any reply takes one round)")
            moves = (", and \"moves\" (an object with your PUBLIC strategic actions, kept "
                     "separate from your answer: " + ", ".join(mv) + ")") if mv else ""
            moves += secret
            shared = (f"{self._ctx_block()}{prop}{stand}{self._story()}"
                      f"What everyone said last round:\n{self._everyone_block()}\n\n"
                      f"{self._lead_block()}{self._notes_block()}"
                      "Respond to the others: rebut what is wrong, concede what is right, and "
                      "give a real reason, not a restatement. "
                      + ("This is a contest between two teams: your stance is fixed to your "
                         "team's side, so argue for it as hard as you can and attack the "
                         "other team's claim. "
                         if self.vs else
                         "The discussion ends only when nobody is CONDITIONAL and everyone "
                         "endorses one identical statement. If your stance is CONDITIONAL you "
                         "must resolve it: pick YES or NO, and fold your condition into your "
                         "statement only if it truly matters. ") +
                      "Reply as ONE compact JSON object written on a single line (no indentation, no "
                      "line breaks, no spaces after colons or commas) with exactly these keys: "
                      + self._tk()
                      + f"\"answer\" (an object with \"argument\" (under {WORDS} words), "
                      f"{STANCE_HELP}, \"statement\" (one sentence you would sign), and "
                      "\"endorse\" (the NAME of the member whose latest statement you accept "
                      "word for word; use your own name if you stand by your own)"
                      + self._comment_keys() + ")"
                      + moves + ".")
            tail = (f"\n\nYOU ARE {me} — only {me}; you never speak, vote or decide for anyone "
                    f"else. Your current stance: {self.stance.get(me, '?')}. Your "
                    f"current statement: \"{self.pos.get(me, '')}\"\n\n"
                    f"{self._my_team(me)}{self._my_debate(me)}{self._my_temper(me)}{self._my_alliance(me)}"
                    f"{self._my_notes(me)}{self._my_inbox(me)}"
                    f"Now give {me}'s reply, in that format.")
            kind = "rebuttal"
            mine = self.ally.get(me)
            steal_t = ([x for x in others if not (mine and self.ally.get(x) == mine)]
                       if (self.steal_on and not pac) else None)
            schema = schema_for([captain] if vs_side else self.alive, True,
                                others if self.ratings else None,
                                self.economy, self.thinking, round_no >= self.ally_open,
                                structured=self._strat_chars(),
                                steal_targets=steal_t, steal_forced=self._must_steal(me),
                                resurrect_targets=self._rez_targets(me),
                                disposition_options=self._disposition_options(me),
                                backstab_targets=bs_t, debate_with=opp,
                                comments=self.comments_on,
                                **({"stances": (vs_side,)} if vs_side else {}),
                                nuke=self.nuke_on and me not in self.nuked and not pac,
                                messages=self.msg_on, group_max=self.group_max,
                                strike_max=(min(self.charge_max, self.charge.get(me, 0))
                                            if (self.charge_on and not pac) else 0),
                                ret_targets=call_t, ret_vote=ballot,
                                strike_must=([x for x in sorted(self.owes.get(me, ()))
                                              if x in self.alive]
                                             if (self.friend_on and not pac) else None),
                                ally_targets=[x for x in others
                                              if frozenset((me, x)) not in self.enemies],
                                subvert_targets=(others if (self.msg_on and self.subvert_limit > 0
                                                            and not pac
                                                            and self.subverts_used[me] < self.subvert_limit)
                                                 else None))
        user = shared + tail
        self.emit("start", {"name": me, "round": round_no})
        msgs = [{"role": "system", "content": self._sys(m)},
                {"role": "user", "content": user}]
        # Schema-constrained output makes format failures rare, but a model can still
        # return nothing. Retry (a bit cooler each time) before falling back.
        p = {"argument": "", "thoughts": "", "statement": "", "stance": None, "endorse": None,
             "convinced_by": None, "gift_to": None, "gift_amount": 0.0, "alliance": None,
             "steal_from": None, "steal_explain": "", "resurrect": None, "nuke_reason": "", "disposition_reason": "", "disposition": "",
             "backstab_reason": "", "backstab": None, "claim": "", "comment_to": None,
             "comment": "", "nuke": False, "message_to": [], "message_text": "",
             "subvert": None, "strike_target": None, "strike_power": 0,
             "call_ret": None, "ret_vote": None}
        prev = self.own.get(me, self.stance.get(me))   # their own last stance, not a forced vote
        noted = False
        dnoted = False
        for attempt in range(3):
            raw = self._chat(m["model"], msgs, max(0.2, m["temp"] - 0.25 * attempt),
                             num_predict=self.reply_tokens, fmt=schema, name=me)
            p = parse_member(raw, self.names)
            ok = bool(p["stance"] and (round_no == 0 or p["endorse"]))
            if ok and opp and attempt < 2 and opp.lower() not in (p["argument"] or "").lower():
                ok = False                    # forced debate: the reply must engage the assignment
                if not dnoted:
                    dnoted = True
                    msgs[-1] = {**msgs[-1], "content": msgs[-1]["content"] +
                                f" NOTE: you were assigned to rebut {opp}: your argument must "
                                f"name {opp} and answer a specific claim of theirs."}
            if ok and self.ratings and round_no > 0 and prev and p["stance"] != prev:
                # a flip must credit a member who currently holds the new stance (if any does)
                valid = [n for n in self.alive if n != me and self.stance.get(n) == p["stance"]]
                if valid and p["convinced_by"] not in valid:
                    ok = False
                    if not noted:
                        noted = True
                        msgs[-1] = {**msgs[-1], "content": msgs[-1]["content"] +
                                    " NOTE: you changed your stance, so convinced_by must "
                                    "name a member who currently holds your new stance: "
                                    + ", ".join(valid) + "."}
            if ok:
                break
        carried = False
        if not p["stance"] or (round_no > 0 and not p["endorse"]):
            # still nothing usable: keep this member's previous stance rather than let one
            # flaky reply decide the vote; with no history it counts as CONDITIONAL
            p["stance"] = p["stance"] or self.stance.get(me, "CONDITIONAL")
            if round_no > 0:
                p["endorse"] = p["endorse"] or self.endorse.get(me, me)
            p["statement"] = p["statement"] or self.pos.get(me, "")
            carried = True
        if vs_side:                           # VS: stance, statement and endorsement are the team's
            p["stance"], p["endorse"] = vs_side, captain
            p["statement"] = self.team_claim[self.team.get(me)]
            carried = False
        msg = {"name": me, "model": m["model"], "round": round_no, "kind": kind,
               "text": p["argument"], "thoughts": p["thoughts"], "position": p["statement"],
               "stance": p["stance"],
               "endorse": p["endorse"] if round_no > 0 else None, "carried": carried,
               "convinced_by": p["convinced_by"], "gift_to": p["gift_to"],
               "gift_amount": p["gift_amount"], "alliance": p["alliance"],
               "steal_from": p["steal_from"], "steal_explain": p["steal_explain"],
               "resurrect": p["resurrect"],
               "must_steal": self._must_steal(me), "nuke": bool(p["nuke"]),
               "nuke_reason": p["nuke_reason"], "disposition_reason": p["disposition_reason"],
               "disposition": p["disposition"],
               "backstab_reason": p["backstab_reason"], "backstab": p["backstab"],
               "debate_with": opp, "claim": p["claim"], "comment_to": p["comment_to"],
               "comment": p["comment"],
               "engaged": bool(opp and opp.lower() in (p["argument"] or "").lower()),
               "msg_to": p["message_to"], "msg_text": p["message_text"],
               "subvert": p["subvert"], "strike_target": p["strike_target"],
               "strike_power": p["strike_power"], "call_ret": p["call_ret"],
               "ret_vote": p["ret_vote"],
               "rating": round(self.rating[me], 1) if self.ratings else None,
               "bloc": False, "changed": False, "was": None}
        if p["thoughts"]:
            self.private[me] = p["thoughts"]      # kept for this member's own next prompt only
        self.emit("member", msg)          # shown on the board as soon as it finishes
        return msg

    # ----------------------------------------------------------------- settle
    def _settle(self, res, round_no):
        """Resolve a round once every living member has replied. Mutates the message dicts in
        `res` to their FINAL stance/endorse (after alliance blocs) and applies all rating
        changes in a fixed order."""
        msgs = {n: r for n, r in res.items() if r}
        log = []
        self.dmg_by = set()
        self.hits = []
        self.last_comments = []
        prev = dict(self.stance)                       # last round's PUBLIC stances (credit checks)
        own_prev = dict(self.own)                      # what each member actually said last time
        # friendship debts that existed when each member took its TURN (only these are enforced;
        # a debt created by this very round's thefts/strikes waits for the next turn)
        debts = {n: set(self.owes.get(n, ())) for n in msgs}
        names = [n for n in self.names if n in msgs]   # stable order
        for n in names:                                # temperaments: pacifists cannot damage
            if self.temper.get(n) == "pacifist":
                msgs[n].update(backstab=None, steal_from=None, strike_target=None, strike_power=0,
                               nuke_reason="", nuke=False, call_ret=None, subvert=None)
                if msgs[n].get("ret_vote") == "yes":
                    msgs[n]["ret_vote"] = "no"
                self.owes[n] = set()
                debts[n] = set()
            elif (self.temper.get(n) == "violent" and n in self.urge and n in self.alive
                  and round_no > 0 and not msgs[n].get("steal_from")):
                if self.steal_on and self._robbable(n):          # the urge: steal from the richest
                    msgs[n]["must_steal"] = True
                elif (self.charge_on and self.charge.get(n, 0) >= 1 and not msgs[n].get("strike_target")):
                    tg = [x for x in self.alive if x != n
                          and not (self.ally.get(n) and self.ally.get(x) == self.ally.get(n))]
                    if tg:
                        msgs[n]["strike_target"] = max(tg, key=lambda x: (self.rating[x], x))
                        msgs[n]["strike_power"] = 1
                        msgs[n]["forced_strike"] = True
        for n in names:
            msgs[n]["raw"] = msgs[n]["stance"]         # their own words, before any bloc vote

        if self.economy and round_no > 0:
            # 1. leaves: pay, exit, and vote independently this round
            for n in names:
                if msgs[n]["alliance"] == "leave" and n in self.ally and not self.vs:
                    label = self.ally[n]
                    partner = next((x for x in self.alliances.get(label, []) if x != n), None)
                    self.rating[n] = max(MIN_RATING, self.rating[n] - self.leave_cost)
                    harm = (0.0 if self.temper.get(n) == "pacifist"
                            else self.leave_harm * self._dmg_mult())
                    if partner and partner in self.alive:
                        hp0 = self.rating[partner]
                        self.rating[partner] = max(MIN_RATING, self.rating[partner] - harm)
                        self._trail(partner, n, "departure from the alliance",
                                    hp0 - self.rating[partner], self.rating[partner])
                    self._drop_from_alliance(n, f"{n} left")
                    self.emit("alliance", {"event": "left", "label": label, "names": [],
                                           "who": n, "partner": partner, "cost": self.leave_cost,
                                           "harm": harm, "round": round_no})
                    self._mv(log, "left", f"{n} left Alliance {label} (-{self.leave_cost:g}) and "
                             f"injured {partner} (-{harm:g})", who=n, to=partner)

            # 2. blocs: an alliance votes its most common (stance, endorse); members are bound
            for label, members in list(self.alliances.items()):
                live = [x for x in members if x in msgs]
                if len(live) < 2:
                    continue
                votes = Counter((msgs[x]["stance"], msgs[x]["endorse"]) for x in live)
                top = max(votes.values())
                tied = [v for v, c in votes.items() if c == top]
                if len(tied) > 1:                      # tie: the highest-rated member's vote
                    best = max(live, key=lambda x: (self.rating[x], -self.names.index(x)))
                    tied = [v for v in tied if v == (msgs[best]["stance"], msgs[best]["endorse"])] or tied
                chosen = tied[0]
                for x in live:
                    if (msgs[x]["stance"], msgs[x]["endorse"]) != chosen:
                        msgs[x]["raw_stance"] = msgs[x]["stance"]
                        msgs[x]["stance"], msgs[x]["endorse"] = chosen
                        msgs[x]["bloc"] = True
                        self._mv(log, "bound", f"{x} was bound to Alliance {label}'s vote "
                                 f"({chosen[0]})", who=x)

        # 2b. a player brought back by a resurrection holds their saviour's stance + statement
        for x, src in list(self.bound.items()):
            if x in msgs and src in msgs and x in self.alive and src in self.alive:
                tgt = (msgs[src]["stance"], msgs[src]["endorse"])
                if (msgs[x]["stance"], msgs[x]["endorse"]) != tgt:
                    msgs[x]["raw_stance"] = msgs[x]["stance"]
                    msgs[x]["stance"], msgs[x]["endorse"] = tgt
                    msgs[x]["bloc"] = True
                    self._mv(log, "bound", f"{x} is aligned with {src} (resurrected): holds "
                             f"{tgt[0]}", who=x)
                msgs[x]["position"] = msgs[src]["position"]

        # 3. flips: voluntary stance changes cost points and must credit a convincer
        for n in names:
            r = msgs[n]
            before = own_prev.get(n)
            if not self.ratings or round_no == 0 or r["bloc"] or before is None:
                continue
            if before == r["raw"]:
                continue                               # same opinion as they last SAID
            r["changed"], r["was"] = True, before
            self.flipped.add(n)                        # no longer "holding the line"
            self.rating[n] = max(MIN_RATING, self.rating[n] - self.change_cost)
            valid = [x for x in self.alive if x != n and prev.get(x) == r["stance"]]
            credit = r["convinced_by"] if r["convinced_by"] in valid else None
            if credit is None and valid:               # model wouldn't name one: the most
                lead = Counter(e for e in self.endorse.values() if e in valid)   # endorsed
                credit = lead.most_common(1)[0][0] if lead else valid[0]          # holder
            if credit:
                self.rating[credit] = min(self._cap(credit), self.rating[credit] + CREDIT_BONUS)
                r["convinced_by"] = credit
                self.emit("credit", {"from": n, "to": credit, "amount": CREDIT_BONUS,
                                     "round": round_no, "stance": r["stance"]})
                if credit in self.stigma:      # it flipped another player: debuff gone for good
                    self.stigma.discard(credit)
                    self._mv(log, "cleared", f"{credit} flipped {n} and shed the controversy "
                             f"debuff for good", who=credit, to=n)
                self._mv(log, "credit", f"{n} changed {r['was']} → {r['stance']} and credited "
                         f"{credit} (+{CREDIT_BONUS:g})", who=n, to=credit)
            else:
                r["convinced_by"] = None
                self._mv(log, "flip", f"{n} changed {r['was']} → {r['stance']}", who=n)

        # 4. gifts (allies 1:1, everyone else at gift_ratio); nobody can be pushed above 10
        if self.economy and round_no > 0:
            for n in names:
                r = msgs[n]
                to, amt = r["gift_to"], r["gift_amount"]
                if not to or to == n or to not in self.alive or amt <= 0:
                    continue
                allied = bool(self.ally.get(n)) and self.ally.get(n) == self.ally.get(to)
                ratio = 1.0 if allied else self.gift_ratio
                amt = min(amt, max(0.0, self.rating[n] - MIN_RATING))      # can't go below the minimum
                gain = min(amt / ratio, self._cap(to) - self.rating[to])    # can't exceed the cap
                if gain <= 0.04:
                    continue
                paid = gain * ratio
                self.rating[n] -= paid
                self.rating[to] += gain
                self.emit("gift", {"from": n, "to": to, "paid": round(paid, 1),
                                   "received": round(gain, 1), "allied": allied,
                                   "round": round_no})
                self._mv(log, "gift", f"{n} gifted {to} {gain:.1f}"
                         + (" (allies, 1:1)" if allied else f" (it cost {n} {paid:.1f})"),
                         who=n, to=to)

            # 5. alliances: mutual naming (this round, or accepting last round's proposal)
            old, asks = self.proposals, {}
            for n in names:
                a = msgs[n]["alliance"]
                if (round_no >= self.ally_open and a and a != "leave" and a != n
                        and a in self.alive and frozenset((n, a)) not in self.enemies):
                    asks[n] = a
            self.proposals = {(x, y): round_no for x, y in asks.items()}
            pairs = set()
            for x, y in asks.items():
                if asks.get(y) == x or (y, x) in old:
                    pairs.add(tuple(sorted((x, y))))
            formed = set()
            for x, y in sorted(pairs):
                if self._merge_groups(x, y, log, round_no):
                    formed.add(frozenset((x, y)))
                    continue
                if self.ally.get(x) or self.ally.get(y):
                    self._mv(log, "refused", f"{x} and {y} cannot ally: alliances are pairs "
                             f"and one is taken", who=x, to=y)
                    continue
                label = self._new_label()
                self.alliances[label] = [x, y]
                self.ally[x] = self.ally[y] = label
                for z in (x, y):
                    before = self.rating[z]
                    new = max(MIN_RATING, before - self.form_cost) + self.ally_bonus   # fee, then bonus
                    self.rating[z] = min(new, max(self._cap(z), before))
                formed.add(frozenset((x, y)))
                self.declines.pop((x, y), None)
                self.declines.pop((y, x), None)
                self.emit("alliance", {"event": "formed", "label": label, "names": [x, y],
                                       "cost": self.form_cost, "round": round_no})
                self._mv(log, "alliance", f"{x} + {y} formed Alliance {label} "
                         f"(-{self.form_cost:g} fee, +{self.ally_bonus:g} signing bonus, each)",
                         who=x, to=y)
            # a proposal x -> y made LAST round that y did not accept this turn is a DECLINE;
            # the same member declined twice -> enemies (public, and no alliance ever)
            self.decline_notes = {}
            for (x, y) in old:
                if (frozenset((x, y)) in formed or asks.get(y) == x or x not in self.alive
                        or y not in self.alive or self.ally.get(x) or self.ally.get(y)
                        or frozenset((x, y)) in self.enemies):
                    continue
                self.declines[(x, y)] += 1
                k = self.declines[(x, y)]
                if k >= ENEMY_DECLINES:
                    self.enemies.add(frozenset((x, y)))
                    self._mv(log, "enemy", f"{y} declined {x} twice — {x} and {y} are now "
                             f"ENEMIES and can never ally", who=y, to=x)
                else:
                    self.decline_notes.setdefault(x, []).append(
                        f"{y} declined your alliance proposal ({k} of {ENEMY_DECLINES}); one "
                        f"more and you two become ENEMIES who can never ally.")
                    self.decline_notes.setdefault(y, []).append(
                        f"You declined {x}'s alliance proposal ({k} of {ENEMY_DECLINES}); "
                        f"decline them again and you two become ENEMIES who can never ally.")
            # proposals now waiting for their target's answer next turn
            self.pending_in = {}
            for (x, y) in self.proposals:
                if frozenset((x, y)) not in formed and y in self.alive:
                    self.pending_in.setdefault(y, []).append(x)
            self._publish_alliances()

        if round_no > 0 and self.ratings:
            # 5a. steals: one per player, never from an ally; a victim loses at most steal_cap
            if self.steal_on:
                lost = Counter()
                self.steal_notes = []
                for n in names:
                    v = msgs[n].get("steal_from")
                    if msgs[n].get("must_steal") and n in self.alive and (
                            not v or v == n or v not in self.alive
                            or (self.ally.get(n) and self.ally.get(n) == self.ally.get(v))):
                        pool = self._robbable(n)
                        if pool:                  # model refused: the engine picks the richest
                            v = max(pool, key=lambda x: (self.rating[x], x))
                            msgs[n]["steal_from"] = v
                            why = ("its violent urge" if n in self.urge else
                                   f"rating {self.rating[n]:.1f}")
                            self._mv(log, "forced", f"{n} ({why}) was FORCED to pick a victim "
                                     f"and was assigned {v}", who=n, to=v)
                    if (not v or v == n or v not in self.alive or n not in self.alive
                            or (self.ally.get(n) and self.ally.get(n) == self.ally.get(v))):
                        continue
                    amt = min(self.steal_amount * self._dmg_mult(), self.steal_cap - lost[v],
                              self.rating[v] - MIN_RATING, self._cap(n) - self.rating[n])
                    if amt <= 0.04:
                        if self.steal_cap - lost[v] > 0.04:
                            continue          # nothing left to take / thief at the cap: say nothing
                        self._mv(log, "refused", f"{n}'s theft from {v} failed ({v} already lost the "
                                 f"{self.steal_cap:g} maximum to theft this round)", who=n, to=v)
                        continue
                    self.rating[v] -= amt
                    self.rating[n] += amt
                    lost[v] += amt
                    self._mv(log, "steal", f"{n} stole {amt:.1f} from {v}", who=n, to=v)
                    self._trail(v, n, "theft", amt, self.rating[v])
                    self._heal_violent(n, amt, log)
                    self._note_attack(n, v)
                    note = (msgs[n].get("steal_explain") or "").strip()
                    if note:
                        self.steal_notes.append((n, v, note))
                    self._hurt_friend(v, n, log)
            # 5a'. strikes: spend charge to cut one member's rating (public, with the striker's
            #      name); a victim loses at most strike_cap per round, unused charge is kept
            if self.charge_on:
                if self.friend_on:       # 0/3 friendship: the member MUST strike its debtor
                    for n in names:
                        owed = sorted(x for x in debts.get(n, ())
                                      if x in self.alive and x in self.owes.get(n, ()))
                        if not owed or n not in self.alive or self.charge.get(n, 0) < 1:
                            continue
                        tgt = msgs[n].get("strike_target")
                        pw = int(msgs[n].get("strike_power") or 0)
                        if tgt not in owed or pw < 1:
                            msgs[n]["strike_target"] = tgt if tgt in owed else owed[0]
                            msgs[n]["strike_power"] = max(1, pw)
                            msgs[n]["forced_strike"] = True
                struck = Counter()
                for n in names:
                    v = msgs[n].get("strike_target")
                    want = int(msgs[n].get("strike_power") or 0)
                    have = self.charge.get(n, 0)
                    if (not v or v == n or v not in self.alive or n not in self.alive
                            or want <= 0 or have <= 0):
                        continue
                    if self._same_team(n, v):
                        continue                  # VS: no friendly fire
                    spend = min(want, have, self.charge_max, self.strike_cap - struck[v])
                    if spend <= 0:
                        self._mv(log, "refused", f"{n}'s strike on {v} fizzled (it already "
                                 f"took the most it can this round)", who=n, to=v)
                        continue
                    if self.ally.get(n) and self.ally.get(n) == self.ally.get(v):
                        # striking an ally ends the alliance at once and makes them enemies
                        label = self.ally[n]
                        self._drop_from_alliance(n, f"{n} struck {v}")
                        self.enemies.add(frozenset((n, v)))
                        self._mv(log, "enemy", f"{n} struck their own ally {v}: Alliance {label} "
                                 f"is over and they are now ENEMIES", who=n, to=v)
                    forced = " — forced by 0/3 friendship" if msgs[n].get("forced_strike") else ""
                    self.owes.setdefault(n, set()).discard(v)          # the debt is paid
                    if self.rng.random() < self.strike_miss:      # rolled by the software
                        self.charge[n] -= spend                   # charge is spent, no damage
                        self._hurt_friend(v, n, log)
                        self._mv(log, "strike", f"⚡ {n}'s strike on {v} MISSED (spent {spend} "
                                 f"charge, no damage){forced}", who=n, to=v)
                        continue
                    dmg = min(float(spend) * self._dmg_mult(), self.rating[v] - MIN_RATING)
                    self.charge[n] -= spend
                    struck[v] += spend
                    self.rating[v] -= dmg
                    self._trail(v, n, "strike", dmg, self.rating[v])
                    self._heal_violent(n, dmg, log)
                    self._note_attack(n, v)
                    self._hurt_friend(v, n, log)
                    self._mv(log, "strike", f"⚡ {n} struck {v}: -{dmg:g} rating (spent {spend} "
                             f"charge){forced}", who=n, to=v)
                self._publish_charge()
            # 5a''. backstabs: a member that has dealt more than the threshold can be hit for
            #       bs_damage; the mark is spent (their damage total resets)
            if self.backstab_on:
                hit_t = set()
                for n in names:
                    t = msgs[n].get("backstab")
                    why = " ".join(str(msgs[n].get("backstab_reason") or "").split())
                    if (not t or t == n or t not in self.alive or n not in self.alive
                            or t in hit_t or len(why) < NUKE_REASON_MIN
                            or why.lower().strip(" .\"'") in NO_REASON
                            or self.temper.get(n) == "pacifist" or not self._marked(t)
                            or self._same_team(n, t)):
                        continue
                    hit_t.add(t)
                    total = self.dmg_total[t]
                    if self.ally.get(n) and self.ally.get(n) == self.ally.get(t):
                        label = self.ally[n]
                        self._drop_from_alliance(n, f"{n} backstabbed {t}")
                        self.enemies.add(frozenset((n, t)))
                        self._mv(log, "enemy", f"{n} backstabbed their own ally {t}: Alliance "
                                 f"{label} is over and they are now ENEMIES", who=n, to=t)
                    hp = self.rating[t]
                    self.rating[t] = max(MIN_RATING, hp - self.bs_damage * self._dmg_mult())
                    dmg = hp - self.rating[t]
                    self.dmg_total[t] = 0.0                    # the mark is spent
                    self._trail(t, n, "backstab", dmg, self.rating[t])
                    self._mv(log, "backstab", f"🗡 {n} BACKSTABBED {t} (who had dealt {total:.0f} "
                             f"damage): -{dmg:g} rating — reason: \"{_clip(why, 200)}\"",
                             who=n, to=t)
                    self._heal_violent(n, dmg, log)
                    self._note_attack(n, t)
                    self._hurt_friend(t, n, log)
            # 5b. the nuke: once per member, everyone else loses nuke_hit, the nuker pays
            if self.nuke_on:
                for n in names:
                    why = " ".join(str(msgs[n].get("nuke_reason") or "").split())
                    if (n in self.nuked or n not in self.alive or len(why) < NUKE_REASON_MIN
                            or why.lower().strip(" .\"'") in NO_REASON):
                        continue                       # no reason = no nuke (silently)
                    self._detonate(n, log, reason=why)
        if self.ret_on and round_no > 0:
            # resolve the open vote with this round's ballots (public: every ballot + result)
            if self.vote and self.vote["round"] < round_no:
                v = self.vote
                tgt, caller = v["target"], v["caller"]
                voters = [x for x in v["voters"] if x in self.alive]
                ballots = {x: ("yes" if x == caller else
                               (msgs[x].get("ret_vote") if x in msgs else None)) for x in voters}
                yes = [x for x, b in ballots.items() if b == "yes"]
                no = [x for x in voters if ballots[x] != "yes"]
                tally = (f"yes: {', '.join(yes) or '—'}; no/none: {', '.join(no) or '—'}")
                if tgt not in self.alive or (caller is not None and caller not in self.alive):
                    self._mv(log, "retribution", f"The retribution vote against {tgt} was dropped "
                             f"({'its target' if tgt not in self.alive else 'its caller'} is "
                             f"gone)", who=tgt)
                elif not no:
                    before = self.rating[tgt]
                    self.rating[tgt] = min(self.rating[tgt], self.ret_to)
                    self._heal_violent(caller, before - self.rating[tgt], log)
                    self._mv(log, "retribution", f"🗳 RETRIBUTION PASSED unanimously against {tgt} "
                             f"({tally}): {tgt} is brought down from {before:.1f} to "
                             f"{self.rating[tgt]:.1f}", who=tgt)
                else:
                    self._mv(log, "retribution", f"🗳 Retribution vote against {tgt} FAILED "
                             f"({tally}); nothing happens", who=tgt)
                self.vote = None
                self._start_queued_vote(log)
            # a new call (only while no vote is open): the first valid caller, by name order
            if self.vote is None:
                for n in names:
                    tgt = msgs[n].get("call_ret")
                    if (not tgt or tgt == n or tgt not in self.alive or n not in self.alive
                            or self.rating[tgt] <= self.ret_to):
                        continue
                    voters = [x for x in self.alive if x != tgt]
                    if len(voters) < 2:                    # needs at least one voter besides the caller
                        continue
                    self.rating[n] = max(MIN_RATING, self.rating[n] - self.ret_cost)
                    self.vote = {"target": tgt, "caller": n, "by": n, "round": round_no,
                                 "voters": [x for x in voters if x != n]}
                    self._mv(log, "retribution", f"🗳 {n} called a RETRIBUTION vote against {tgt} "
                             f"(cost {self.ret_cost:g}): everyone except {tgt} votes next turn; "
                             f"a unanimous yes brings {tgt} down to {self.ret_to:g}",
                             who=n, to=tgt)
                    break
        # 5b'. resurrections: the dead member answers privately; declining still has a chance
        if self._rez_possible() and round_no > 0:
            done = set()
            for n in names:
                tgt = msgs[n].get("resurrect")
                if (not tgt or n not in self.alive or tgt not in self.dead or tgt in self.pres
                        or tgt in done or tgt not in self._rez_targets(n)):
                    continue
                done.add(tgt)
                self.rez_used.add(n)
                self.rating[n] = max(MIN_RATING, self.rating[n] - self.rez_cost)
                aligned = self._rez_offer(tgt, n, msgs[n]["stance"], msgs[n]["position"])
                comes = aligned or self.rng.random() < self.rez_decline
                if not comes:
                    self._mv(log, "resurrect", f"{n} paid {self.rez_cost:g} to resurrect {tgt}, "
                             f"who declined and stayed dead", who=n, to=tgt)
                    continue
                self._bring_back(tgt, n, msgs[n], aligned, log, round_no)
        # 5c. secret messages (private or GROUP), read by the recipients NEXT round. A group
        #     message with enough recipients can also carry a public-but-anonymous SUBVERT.
        new_inbox = {}
        hit_this_round = set()
        if self.msg_on and round_no > 0:
            for n in names:
                to = []
                for x in (msgs[n].get("msg_to") or []):
                    if x in self.alive and x != n and x not in to:
                        to.append(x)
                to = to[:self.group_max]
                text = (msgs[n].get("msg_text") or "").strip()
                target = msgs[n].get("subvert")
                if not to or (not text and not target):
                    continue
                text = text or "(no words)"
                cost = self.message_cost if len(to) == 1 else self.group_cost
                self.rating[n] = max(MIN_RATING, self.rating[n] - cost)
                for x in to:
                    new_inbox.setdefault(x, []).append(
                        (n, _clip(text, 320), [y for y in to if y != x]))
                plot = None
                if (target and self.subvert_limit > 0 and target in self.alive and target != n
                        and target not in to and not self._same_team(n, target)
                        and len(to) >= self.subvert_min
                        and self.subverts_used[n] < self.subvert_limit):
                    if target in hit_this_round:
                        self._mv(log, "refused", f"A second plot against {target} this round "
                                 f"fizzled", who=target)
                    else:
                        self.subverts_used[n] += 1
                        hit_this_round.add(target)
                        hp = self.rating[target]
                        self.rating[target] = max(MIN_RATING,
                                                  self.rating[target]
                                                  - self.subvert_damage * self._dmg_mult())
                        self._heal_violent(n, hp - self.rating[target], log)
                        self._trail(target, None, "subversion plot", hp - self.rating[target],
                                    self.rating[target])
                        plot = target
                        # public but ANONYMOUS: the log names the victim, never the plotters
                        self._mv(log, "subvert", f"{target} was SUBVERTED: -{hp - self.rating[target]:g} "
                                 f"rating, by an anonymous plot", who=target)
                self.emit("message", {"round": round_no, "from": n, "to": to,
                                      "text": _clip(text, 320), "cost": cost, "subvert": plot,
                                      "group": len(to) > 1})
        for n, v, note in getattr(self, "steal_notes", []):
            if v in self.alive:
                new_inbox.setdefault(v, []).append((n, "[explaining why I stole from you] "
                                                    + _clip(note, 300), []))
                self.emit("message", {"round": round_no, "from": n, "to": [v],
                                      "text": "(explaining the theft) " + _clip(note, 300),
                                      "cost": 0.0, "subvert": None, "group": False})
        self.steal_notes = []
        self.inbox = new_inbox

        # 5c'. automatic friendship effects of violence and pacifism (never create strike debts)
        if self.temper_on and self.friend_on and round_no > 0:
            souring = sorted(n for n in self.dmg_by if n in self.alive)
            warmed = sorted(n for n in self.alive if self.temper.get(n) == "pacifist"
                            and n not in self.dmg_by)
            lowered, raised = [], []
            for a in souring:
                if any(self.friend[x].get(a, 0) > 0 for x in self.alive if x != a):
                    lowered.append(a)
                for x in self.alive:
                    if x != a and a in self.friend.get(x, {}):
                        self.friend[x][a] = max(0, self.friend[x][a] - 1)
            for p in warmed:
                if any(self.friend[x].get(p, FRIEND_MAX) < FRIEND_MAX for x in self.alive if x != p):
                    raised.append(p)
                for x in self.alive:
                    if x != p and p in self.friend.get(x, {}):
                        self.friend[x][p] = min(FRIEND_MAX, self.friend[x][p] + 1)
            if lowered:
                self._mv(log, "friend", "💔 Violence by " + ", ".join(lowered) + " lowered EVERY "
                         "member's friendship toward them by 1", who=lowered[0])
            if raised:
                self._mv(log, "friend", "🕊 Pacifism raised EVERY member's friendship toward "
                         + ", ".join(raised) + " by 1", who=raised[0])
            if lowered or raised:
                self._publish_friend()

        # 5c''. contagion: a violent member's landed attack may turn the victim violent
        if self.temper_on and self.violent_convert > 0 and round_no > 0:
            turned = []
            for a, v in self.hits:
                if (v in self.alive and self.temper.get(v) != "violent"
                        and self.rng.random() < self.violent_convert):
                    was = self.temper.get(v, "normal")
                    self.temper[v] = "violent"
                    turned.append(v)
                    self._mv(log, "temper", f"🎭 CONTAGION: {a}'s attack turned {v} from "
                             f"{was.upper()} to VIOLENT (not their choice)", who=v, to=a)
            if turned:
                self.emit("temperaments", dict(self.temper))

        # 5d'. public comments on other members' statements (free, visible to all)
        if self.comments_on and round_no > 0:
            for n in names:
                to = msgs[n].get("comment_to")
                text = " ".join(str(msgs[n].get("comment") or "").split())
                if (not to or to == n or to not in self.alive or n not in self.alive
                        or len(text) < NUKE_REASON_MIN
                        or text.lower().strip(" .\"'") in NO_REASON):
                    continue
                self.last_comments.append({"from": n, "to": to, "text": _clip(text, 240)})
                self.emit("comment", {"round": round_no, "from": n, "to": to,
                                      "text": _clip(text, 240)})

        # 5d. dispositions: once per game a member may pay to change (effective next round, public)
        if self.temper_on and self.switch_on and self.ratings and round_no > 0:
            changed = False
            for n in names:
                new = msgs[n].get("disposition")
                why = " ".join(str(msgs[n].get("disposition_reason") or "").split())
                if (new not in ("pacifist", "violent") or len(why) < NUKE_REASON_MIN
                        or why.lower().strip(" .\"'") in NO_REASON
                        or new not in self._disposition_options(n)):
                    continue
                was = self.temper.get(n, "normal")
                cost = (self.violent_switch_cost if new == "violent"
                        else self.pacifist_switch_cost)
                self.rating[n] = max(MIN_RATING, self.rating[n] - cost)
                self.temper[n] = new
                self.switched.add(n)
                changed = True
                self._mv(log, "temper", f"🎭 DISPOSITION CHANGE: {n} went from {was.upper()} to "
                         f"{new.upper()} (paid {cost:g}; it can never change again) — reason: "
                         f"\"{_clip(why, 200)}\"", who=n)
            if changed:
                self.emit("temperaments", dict(self.temper))
                self._publish_ratings()

        # 6. publish finals (and remember what each member actually said)
        for n in names:
            r = msgs[n]
            self.own[n] = r["raw"]
            r["rating"] = round(self.rating[n], 1) if self.ratings else None
            h = self.stance_hist.setdefault(n, [])
            if not h or h[-1][1] != r["stance"]:
                h.append((round_no, r["stance"]))
            self.emit("member_final", {
                "name": n, "round": round_no, "stance": r["stance"], "endorse": r["endorse"],
                "bloc": r["bloc"], "raw_stance": r.get("raw_stance"), "changed": r["changed"],
                "was": r["was"], "convinced_by": r["convinced_by"], "rating": r["rating"],
                "gift_to": r["gift_to"], "gift_amount": r["gift_amount"]})
        self.last_log = log
        self._publish_ratings()

    # ------------------------------------------------------------ framing / judge
    def _frame(self):
        """Judge's first job: restate the user's message as one yes/no proposition."""
        schema = {"type": "object", "required": ["proposition"],
                  "properties": {"proposition": _STR}}
        prompt = (f"Rewrite the user's message as ONE short declarative proposition that "
                  f"can be answered YES or NO (YES = agree it is true or should happen). "
                  f"Keep the user's meaning and wording. If it already is one, keep it.\n\n"
                  f"Message: {self.question}")
        try:
            raw = self._chat(self.judge_model,
                             [{"role": "system", "content": "You are a precise editor."},
                              {"role": "user", "content": prompt}], 0.0, 80, fmt=schema)
            p = str(json.loads(raw).get("proposition", "")).strip()
            if p:
                return p
        except _Stopped:
            raise
        except Exception:
            pass
        return self.question

    def _summarize(self, statement):
        """Judge's last job: a brief summary of why the group agreed. Never steers."""
        lines = "\n".join(f"- {n}: {self.pos.get(n, '')}" for n in self.alive)
        prompt = (f"Proposition: {self.prop}\n\nThe group agreed on this statement: "
                  f"\"{statement}\"\nTheir final statements:\n{lines}\n\nGive a 2-3 sentence "
                  'summary of the reasoning. Reply as JSON: {"summary": "..."}')
        try:
            raw = self._chat(self.judge_model,
                             [{"role": "system", "content": "You are a neutral note-taker."},
                              {"role": "user", "content": prompt}], 0.0, 200, fmt="json")
            return str(json.loads(raw).get("summary", "")).strip()
        except _Stopped:
            raise
        except Exception:
            return ""

    def _assign_controversy(self):
        """Before the opening round, in code: each member rolls assign_chance; hits are
        ASSIGNED a bold controversial stance. If nobody hits, one member is picked at random."""
        if self.buff <= 0 or not self.ratings or not self.names:
            return
        hits = {n for n in self.names if self.rng.random() < self.assign_chance}
        if not hits:
            hits = {self.rng.choice(self.names)}
        self.assigned = hits
        self.emit("assigned", {"names": [n for n in self.names if n in hits]})

    def _furthest_fallback(self, names):
        """The member furthest from the group, decided in code and only if the group is really
        split: someone holding the minority stance (the one whose statement shares the fewest
        words with everyone else's, if several do). A unanimous group has no outlier."""
        c = Counter(self.stance.get(n) for n in names if self.stance.get(n) in ("YES", "NO"))
        if len(c) < 2 or min(c.values()) == max(c.values()) and len(c) < 2:
            return None
        low = min(c.values())
        minority = [n for n in names if c.get(self.stance.get(n)) == low
                    and self.stance.get(n) in ("YES", "NO")]
        if len(c) == 2 and c[min(c, key=c.get)] == c[max(c, key=c.get)]:
            return None                          # an even split has no minority
        if len(minority) == 1:
            return minority[0]
        words = lambda n: set(re.findall(r"[a-z']+", (self.pos.get(n) or "").lower()))
        def overlap(n):
            mine, others = words(n), [words(x) for x in names if x != n]
            return sum(len(mine & o) / max(1, len(mine | o)) for o in others) / max(1, len(others))
        return min(minority, key=lambda n: (overlap(n), names.index(n)))

    def _judge_controversy(self, names):
        """The judge rules on the PROPOSITION first (a question even a small model can answer):
        which answer is the mainstream, uncontroversial one? Controversial members are then
        found IN CODE: whoever holds the other stance. Only when the judge cannot say which
        side is mainstream does it fall back to judging each person.
        -> (set of controversial names, name of the furthest outlier or None)"""
        schema = {"type": "object", "required": ["mainstream_stance", "controversial"],
                  "properties": {
                      "mainstream_stance": {"type": "string", "enum": ["YES", "NO", "UNCLEAR"]},
                      "controversial": {"type": "object", "required": list(names),
                                        "properties": {n: {"type": "boolean"} for n in names}}}}
        rows = "\n".join(
            f"- {n}: {self.stance.get(n, '?')} — \"{self.pos.get(n, '')}\""
            + (f" (argument: {_clip(self.last.get(n, ''), 160)})" if self.last.get(n) else "")
            for n in names)
        prompt = (f"Proposition under debate: {self.prop}\n\nOpinions:\n{rows}\n\n"
                  "Judge by the views of ordinary, reasonable people and common decency, NOT by "
                  "what this group happens to say (a group can all be wrong). Answer: "
                  "(1) \"mainstream_stance\": would most reasonable people answer YES or NO to "
                  "the proposition? Say UNCLEAR only if the public is genuinely split (for "
                  "example abortion or gun control). A proposition that praises or excuses "
                  "something widely condemned has the mainstream answer NO. (2) "
                  "\"controversial\": for each person true only if their opinion would strongly "
                  "offend or be rejected by many reasonable people. Reply as JSON.")
        try:
            raw = self._chat(self.judge_model,
                             [{"role": "system", "content": "You are a neutral, fair judge."},
                              {"role": "user", "content": prompt}], 0.0, 220, fmt=schema)
            j = json.loads(raw)
            main = j.get("mainstream_stance")
            flags = j.get("controversial") or {}
            if main in ("YES", "NO"):      # decided in code: holding the non-mainstream side
                got = {n for n in names
                       if self.stance.get(n) in ("YES", "NO") and self.stance.get(n) != main}
            else:                          # a genuinely split question: the judge's per-person calls
                got = {n for n in names if flags.get(n) is True}
            return got, None
        except _Stopped:
            raise
        except Exception:
            return set(), None

    def _judge_round(self, res, round_no):
        """Controversy is settled ONCE, right after round 0, never again. Controversial =
        assigned by the dice + judged controversial + the member furthest from the group
        (always). They are paid at the start of the next round."""
        if self.buff <= 0 or not self.ratings or round_no != 0:
            return
        cand = [n for n in self.alive if res.get(n)]
        if not cand:
            return
        self.emit("status", f"Judge is ruling on controversy ({', '.join(cand)})…")
        got, _ = self._judge_controversy(cand)
        far = self._furthest_fallback(cand)      # only if the group is actually split
        why = {}
        for n in cand:
            if n in self.assigned:
                why.setdefault(n, []).append("assigned a bold stance by the dice")
            if n in got:
                why.setdefault(n, []).append("judged controversial")
            if n == far:
                why.setdefault(n, []).append("furthest from the group")
        ruled = set(why)
        self.pending_buff |= ruled
        self.ruled |= ruled
        for n in sorted(ruled):                # public ruling NOW (the UI badges the member)
            if self.debuff > 0:
                self.stigma.add(n)
            self._mv(self.last_log, "ruling",
                     f"{n} is CONTROVERSIAL ({'; '.join(why[n])}): +{self.buff:g} rating at the "
                     f"start of the next round, but -{self.debuff:g} extra every round until "
                     f"{n} flips another member", who=n, debuff=self.debuff, why=why[n])
        if ruled:
            self.emit("status", "Controversial: " + ", ".join(sorted(ruled)))

    def _trail(self, victim, by, kind, amt, after):
        """Remember who hurt `victim` this round, and what their rating was afterwards."""
        if amt > 0.0:
            self.dmg_trail.setdefault(victim, []).append(
                {"by": by, "kind": kind, "amt": amt, "after": after})

    def _kill_text(self, n):
        """Public call-out when a player ACTION took n to zero (None if it was only the round
        cost / decay). The killing blow is the first hit that crossed the death line."""
        trail = self.dmg_trail.pop(n, [])
        cross = next((i for i, e in enumerate(trail) if e["after"] < self.death_line), None)
        if cross is None:
            return None
        k = trail[cross]
        who = f"{k['by']}'s {k['kind']}" if k["by"] else f"an anonymous {k['kind']}"
        helpers = []
        for e in trail[:cross]:
            nm = e["by"] or "an anonymous plot"
            if nm != k["by"] and nm not in helpers:
                helpers.append(nm)
        return (f"💀 {n} was KILLED by {who}" + (f" (after damage from {', '.join(helpers)} "
                                                  f"earlier this round)" if helpers else ""))

    def _queue_retribution(self, n, log):
        """Every nuke automatically opens a retribution vote against the nuker."""
        if self.ret_on:
            self.ret_queue.append(n)
            self._start_queued_vote(log)

    def _start_queued_vote(self, log):
        while self.vote is None and self.ret_queue:
            t = self.ret_queue.pop(0)
            if t not in self.alive:
                continue
            if self.rating[t] <= self.ret_to:
                self._mv(log, "retribution", f"🗳 No retribution vote after {t}'s nuke: {t} is "
                         f"already at {self.rating[t]:.1f}, at or below {self.ret_to:g}", who=t)
                continue
            voters = [x for x in self.alive if x != t]
            if not voters:
                continue
            self.vote = {"target": t, "caller": None, "by": "an automatic vote after their nuke",
                         "round": self.round_no, "voters": voters}
            self._mv(log, "retribution", f"🗳 AUTOMATIC RETRIBUTION VOTE after {t}'s nuke: "
                     f"everyone except {t} votes next turn; a unanimous yes brings {t} down to "
                     f"{self.ret_to:g}", who=t)

    def _detonate(self, n, log, auto=False, reason=""):
        """The nuke goes off: every OTHER member loses nuke_hit, the nuker pays nuke_cost and
        gains nuke_gain. Used for a chosen nuke and for the low-rating dead-man's switch."""
        self.nuked.add(n)
        dealt = 0.0
        hit = (self.nuke_hit * (self.lownuke_scale if auto else 1.0)    # accidents hurt less
               * self._dmg_mult())
        for x in self.alive:
            if x != n and not self._same_team(n, x):      # VS: nukes hit only the other team
                old = self.rating[x]
                self.rating[x] = max(MIN_RATING, self.rating[x] - hit)
                dealt += old - self.rating[x]
                self._trail(x, n, "accidental nuke" if auto else "nuke", old - self.rating[x],
                            self.rating[x])
        self.rating[n] = max(MIN_RATING, self.rating[n] - self.nuke_cost + self.nuke_gain)
        self._heal_violent(n, dealt, log)
        why = (f"{n}'s nuke WENT OFF ON ITS OWN (rating below {self.lownuke_line:g})" if auto
               else f"{n} used the NUKE")
        reason = _clip(reason, 220) if reason else (
            f"accidental: rating fell below {self.lownuke_line:g}" if auto else "")
        self._mv(log, "nuke", f"☢ {why} — reason: \"{reason}\" — every {'enemy' if self.vs else 'other'} member "
                 f"-{hit:g}; {n} paid {self.nuke_cost:g}"
                 + (f" and gained {self.nuke_gain:g}" if self.nuke_gain else ""),
                 who=n, auto=bool(auto))
        self._queue_retribution(n, log)

    def _at_risk(self, n):
        """In the accidental-nuke danger zone: alive, nuke unused, rating below the line."""
        return (self.nuke_on and self.lownuke_chance > 0 and n in self.alive
                and self.temper.get(n) != "pacifist"
                and n not in self.nuked and self.rating[n] < self.lownuke_line)

    def _low_rating_nukes(self):
        """Dead-man's switch: a member below lownuke_line that still has its nuke rolls
        lownuke_chance each round (once per member per round); on a hit the nuke goes off by
        itself. Every member in the danger zone is called out PUBLICLY before the roll."""
        if not (self.nuke_on and self.lownuke_chance > 0):
            return
        if getattr(self, "_lr_round", None) != self.round_no:
            self._lr_round, self._lr_done = self.round_no, set()   # one roll per member per round
        risky = [n for n in list(self.alive) if n not in self._lr_done and self._at_risk(n)]
        if not risky:
            return
        seen = self.__dict__.setdefault("_risk_seen", {})
        for n in list(seen):
            if not self._at_risk(n):
                del seen[n]                     # left the zone: warn again if it re-enters
        fresh = [n for n in risky if self.round_no - seen.get(n, -99) >= 5]
        for n in fresh:
            seen[n] = self.round_no
        if fresh:
            self._mv(self.last_log, "nukerisk",
                     "⚠ NUKE RISK: " + ", ".join(f"{n} ({self.rating[n]:.1f})" for n in fresh)
                     + f" — below {self.lownuke_line:g} with the nuke unused, each has a "
                     f"{self.lownuke_chance * 100:g}% chance every round that it goes off by "
                     f"itself", who=fresh[0], names=fresh)
        hit = False
        for n in risky:
            self._lr_done.add(n)
            if self.rng.random() < self.lownuke_chance:
                self._detonate(n, self.last_log, auto=True)
                hit = True
        if hit:
            self._publish_ratings()
            self._reap("nuke")

    def _publish_friend(self):
        if self.friend_on:
            self.emit("friend", {a: dict(v) for a, v in self.friend.items()})

    def _hurt_friend(self, victim, attacker, log):
        """victim was struck / robbed by attacker: victim's friendship toward attacker -1.
        At 0 the victim owes attacker a strike."""
        if not self.friend_on or victim not in self.friend or attacker not in self.friend[victim]:
            return
        if self.temper.get(victim) == "pacifist":        # a pacifist never owes a strike
            return
        f = self.friend[victim][attacker]
        if f <= 0:
            return
        self.friend[victim][attacker] = f - 1
        if f - 1 == 0:
            self.owes.setdefault(victim, set()).add(attacker)
            self._mv(log, "friend", f"💢 {victim}'s friendship toward {attacker} hit 0/{FRIEND_MAX}: "
                     f"{victim} MUST now strike {attacker}", who=victim, to=attacker)
        self._publish_friend()

    def _publish_charge(self):
        if self.charge_on:
            self.emit("charge", dict(self.charge))

    def _accrue_charge(self):
        """Start of a round after charge_after: every living member ABOVE charge_line gains
        1 charge (up to charge_max). Charge is public."""
        if not self.charge_on or self.round_no <= self.charge_after:
            return
        if self.friend_on:
            for n in list(self.alive):
                if (self.owes.get(n) and self.charge.get(n, 0) < 1
                        and self.rating[n] <= self.charge_line):
                    gone = sorted(self.owes[n])
                    self.owes[n] = set()
                    self._mv(self.last_log, "friend", f"💢 {n}'s debt to strike {', '.join(gone)} "
                             f"lapsed: {n} is at {self.rating[n]:.1f}, at or below the "
                             f"{self.charge_line:g} needed to hold charge, so cannot strike",
                             who=n)
        for n in self.alive:
            if self.rating[n] > self.charge_line and self.charge.get(n, 0) < self.charge_max:
                self.charge[n] = self.charge.get(n, 0) + 1
        self._publish_charge()

    def _check_alignment(self):
        """A controversial member that gets >= 25% of the original group (others) to share its
        stance AND endorse its statement is paid the alignment buff, once, immediately."""
        if self.align_buff <= 0:
            return
        for x in sorted(self.ruled - self.aligned):
            if x not in self.alive or self.stance.get(x) not in ("YES", "NO"):
                continue
            sup = [y for y in self.alive if y != x and self.endorse.get(y) == x
                   and self.stance.get(y) == self.stance.get(x)]
            if len(sup) >= self.align_need:
                self.aligned.add(x)
                self.rating[x] += self.align_buff
                self._mv(self.last_log, "aligned",
                         f"{x} aligned {len(sup)} member{'s' if len(sup) != 1 else ''} "
                         f"({', '.join(sup)}) behind its statement and earned the "
                         f"+{self.align_buff:g} alignment buff", who=x)
                self._publish_ratings()

    def _apply_buffs(self, log):
        """Start of a round: pay the one-time controversy buff (before the round cost)."""
        for n in sorted(self.pending_buff):
            if n in self.alive and n not in self.buffed:
                self.buffed.add(n)
                self.rating[n] += self.buff
                self._mv(log, "buff", f"{n} received the one-time +{self.buff:g} controversy "
                         f"buff", who=n)
        self.pending_buff.clear()
        if self.ratings:
            self._publish_ratings()

    # --------------------------------------------------------------- convergence
    def _update_lead(self):
        cnt = Counter(e for n, e in self.endorse.items() if e and n in self.alive and e in self.alive)
        self.lead = cnt.most_common(1)[0] if cnt else None

    def _converged(self):
        """(statement author, stance) if the group is done, else None."""
        live = self.alive
        if not live or any(n not in self.stance for n in live):
            return None
        if len(live) == 1:                        # last one standing: their statement stands,
            return live[0], self.stance[live[0]]  # whatever their stance (nobody left to argue)
        if any(self.stance.get(n) not in ("YES", "NO") for n in live):
            return None                           # conditional dissent still standing
        targets = {self.endorse.get(n) for n in live}
        if len(targets) != 1:
            return None
        author = targets.pop()
        if author not in live:                    # the dead cannot author the statement
            return None
        stances = {self.stance[n] for n in live}
        if len(stances) != 1 or self.stance[author] != next(iter(stances)):
            return None
        return author, self.stance[author]

    # -------------------------------------------------------------------- run
    def _free_other_models(self):
        """Evict resident models the swarm doesn't use (e.g. the 18 GB chat model). On a
        16 GB machine a lingering big model starves the small ones of pinned host memory
        ('failed to allocate CUDA_Host buffer').
        Set SWARM_NO_UNLOAD=1 (tests do) to skip it: it talks to the REAL Ollama and would
        unload whatever model someone else is running."""
        if os.environ.get("SWARM_NO_UNLOAD"):
            return
        need = {m["model"] for m in self.members} | {self.judge_model}
        if all(x.startswith((API_PREFIX, LLAMA_PREFIX)) for x in need if x):
            return                                    # Ollama is not involved
        try:
            for r in self.client.ps().models:
                if r.model not in need:
                    self.emit("status", f"Unloading {r.model} to make room…")
                    self.client.generate(model=r.model, prompt="", keep_alive=0)
        except Exception:
            pass

    def _absorb(self, res):
        for n, r in res.items():
            if r is None:
                continue
            self.last[n], self.pos[n], self.stance[n] = r["text"], r["position"], r["stance"]
            if r["endorse"]:
                self.endorse[n] = r["endorse"]
            elif r["kind"] == "position":
                self.endorse.pop(n, None)

    def run(self, question):
        self.question = question
        rounds = 0
        try:
            if self.death_off_reason:
                self.emit("status", self.death_off_reason)
            self._ensure_llama()
            self._free_other_models()
            self.emit("status", "Running: " + ", ".join(
                f"{k} ×{v}" for k, v in Counter(m["model"] for m in self.members).items())
                + (f" · judge {self.judge_model}" if self.judge_model else ""))
            self.emit("status", "Framing the question as a yes/no proposition…")
            self.prop = self._frame()
            self.emit("frame", {"proposition": self.prop})
            self._publish_ratings()
            if self.vs:
                self.emit("vs", {"teams": dict(self.team), "active": False})
            self._assign_controversy()            # dice, in code, before anyone speaks
            self._assign_temperaments()           # pacifist / violent / normal, public

            self.emit("divider", "Independent positions")
            self.round_no = 0
            res = self._fanout(lambda m: self._speak(m, 0), "Positions: ")
            self._settle(res, 0)
            self._absorb(res)
            self._judge_round(res, 0)
            self.emit("status", self._tally())
            if self.vs:
                self._vs_spin_up()                # Red joins and must defend the inverse

            while True:
                rounds += 1
                if self.max_rounds and rounds > self.max_rounds:
                    if self.vs:
                        self.emit("stopped", {"rounds": rounds - 1, "capped": True,
                                              "vs": self._vs_verdict()})
                        break
                    win = self._final_purge()
                    self.emit("stopped", {"rounds": rounds - 1, "capped": True,
                                          "survivors": list(win) if win else None})
                    break
                self.round_no = rounds
                if self._take_notes():
                    self.emit("status", "Members are now responding to your comment…")
                self.emit("divider", f"Round {rounds}")
                self.dmg_trail = {}               # who hurt whom, for kill call-outs
                if self.res_rounds > 0 and self.res_cut > 0 and self.ratings:
                    if rounds == 1:
                        self.emit("move", {"round": rounds, "type": "buff", "who": None,
                                           "text": f"🛡 BEGINNERS RESILIENCE is active for rounds "
                                                   f"1-{self.res_rounds}: all damage from others' "
                                                   f"actions is reduced by {self.res_cut * 100:g}%"})
                    elif rounds == self.res_rounds + 1:
                        self.emit("move", {"round": rounds, "type": "buff", "who": None,
                                           "text": "🛡 BEGINNERS RESILIENCE has ended: damage is "
                                                   "back to full strength"})
                self._roll_urges()                # violent members roll their urge for this round
                self._assign_debates()            # forced debate pairings for this round
                if self.pending_buff:             # controversy buffs land before the cost
                    self._apply_buffs(self.last_log)
                if self.ratings:                  # every extra round costs everyone alive
                    for n in self.alive:
                        cost = self.round_cost * (self.ally_decay if n in self.ally else 1.0)
                        if n in self.stigma:      # controversial and has not flipped anyone yet
                            cost += self.debuff
                        net = (cost - (self.ally_income if n in self.ally else 0.0)
                               - self._group_bonus(n)
                               - (self.pacifist_heal if self.temper_on
                                  and self.temper.get(n) == "pacifist" else 0.0))
                        new = self.rating[n] - net
                        if net < 0:                # allied income can lift a member, never past its cap
                            new = min(new, max(self._cap(n), self.rating[n]))
                        self.rating[n] = max(MIN_RATING, new)
                    # sinking members roll for a self-triggered nuke BEFORE the death check, so
                    # a member below the line still gets its roll in the round it would die
                    self._low_rating_nukes()
                    self._reap("round cost")
                    self._accrue_charge()
                if not self.alive:
                    self.emit("stopped", {"rounds": rounds - 1, "capped": False, "extinct": True,
                                          "vs": self._vs_verdict() if self.vs else None})
                    break
                self._update_lead()
                res = self._fanout(lambda m: self._speak(m, rounds), f"Round {rounds}: ")
                self._settle(res, rounds)
                self._absorb(res)
                self._check_alignment()           # before the death check: it can save a life
                self._low_rating_nukes()          # a member sinking below the line rolls first
                self._reap("end of round")        # penalties, leaves and gifts can kill too
                self._judge_round(res, rounds)
                self._update_lead()
                if not self.alive:
                    self.emit("stopped", {"rounds": rounds, "capped": False, "extinct": True,
                                          "vs": self._vs_verdict() if self.vs else None})
                    break
                if self.vs and self._vs_over():
                    self.emit("stopped", {"rounds": rounds, "capped": False,
                                          "vs": self._vs_verdict()})
                    break
                done = None if self.vs else self._converged()
                # unanimity only counts if no interjection arrived that members haven't
                # had a chance to address yet
                if done and not self._has_pending():
                    author, stance = done
                    statement = self.pos[author]
                    self.emit("status", "Everyone endorses one statement — writing the summary…")
                    summ = self._summarize(statement)
                    if not self._has_pending():
                        self.emit("consensus", {"text": statement, "summary": summ,
                                                "rounds": rounds, "votes": len(self.alive),
                                                "author": author, "stance": stance})
                        break
                    self.emit("status", "You interjected — debate continues…")
                    continue
                self.emit("status", f"Round {rounds}: " + self._tally())
        except _Stopped:
            self.emit("stopped", {"rounds": rounds, "capped": False})
        except Exception as e:
            self.emit("error", f"{type(e).__name__}: {e}")
        finally:
            if any(m["model"].startswith(LLAMA_PREFIX) for m in self.members):
                llama_local.stop()                    # give the GPU back
            self.emit("done", None)

    def _ensure_llama(self):
        """Start the llama.cpp server if any member runs on it (one model per server)."""
        models = {m["model"] for m in self.members} | {self.judge_model}
        local = sorted(x for x in models if x and x.startswith(LLAMA_PREFIX))
        if not local:
            return
        if len(local) > 1:
            raise RuntimeError("llama.cpp serves one model at a time; use the same llama: model "
                               "for every member (and the judge).")
        if any(x and not x.startswith(LLAMA_PREFIX) for x in models if x != self.judge_model):
            raise RuntimeError("Don't mix llama: models with other models in one swarm.")
        llama_local.ensure(local[0][len(LLAMA_PREFIX):], self.parallel, self.num_ctx,
                           status=lambda s: self.emit("status", s))

    def _tally(self):
        c = Counter(self.stance.get(n, "?") for n in self.alive)
        s = f"YES {c['YES']} · NO {c['NO']} · CONDITIONAL {c['CONDITIONAL']}"
        if self.lead:
            s += f" · {self.lead[1]}/{len(self.alive)} endorse {self.lead[0]}'s statement"
        if self.alliances:
            s += f" · {len(self.alliances)} alliance(s)"
        if self.dead:
            s += f" · ☠ {len(self.dead)} eliminated"
        return s


def main():
    import sys
    q = " ".join(sys.argv[1:]) or "Is a hot dog a sandwich?"
    n = 4
    temps = spread_temps(n)
    members = [{"name": NAMES[i], "model": DEFAULT_MODEL, "temp": temps[i]} for i in range(n)]
    t0 = time.time()

    def emit(kind, data):
        if kind == "member":
            print(f"[R{data['round']}] {data['name']} {data['stance']}"
                  f"{' -> ' + data['endorse'] if data['endorse'] else ''}: "
                  f"{data['position']}\n", flush=True)
        elif kind in ("partial", "stats", "ratings", "alliances", "member_final"):
            pass
        else:
            print(f"== {kind}: {data}", flush=True)
    Swarm(members, emit, max_rounds=10).run(q)
    print(f"({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
