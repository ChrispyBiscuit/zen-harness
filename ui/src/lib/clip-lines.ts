/** What Clip (the paperclip, components/Clip.tsx) says: a line of philosophy (Nietzsche and some 30 others), and
 *  queries worth trying. Quotes are short paraphrases of public-domain or classic sources. */
import type { Conv } from "./types"

export const CLIP_QUOTES: { quote: string; by: string }[] = [
  { quote: "What does not kill me makes me stronger.", by: "Nietzsche" },
  { quote: "He who has a why can bear almost any how.", by: "Nietzsche" },
  { quote: "Gaze long into an abyss and the abyss gazes back.", by: "Nietzsche" },
  { quote: "God is dead.", by: "Nietzsche" },
  { quote: "Without music, life would be a mistake.", by: "Nietzsche" },
  { quote: "Become who you are!", by: "Nietzsche" },
  { quote: "One must have chaos within to birth a dancing star.", by: "Nietzsche" },
  { quote: "Amor fati: love your fate.", by: "Nietzsche" },
  { quote: "Would you live this moment again, forever?", by: "Nietzsche" },
  { quote: "There are no facts, only interpretations.", by: "Nietzsche" },
  { quote: "The higher we soar, the smaller we look to those who cannot fly.", by: "Nietzsche" },
  { quote: "Live dangerously!", by: "Nietzsche" },
  { quote: "Whoever fights monsters should not become one.", by: "Nietzsche" },
  { quote: "Madness is rare in individuals, the rule in groups.", by: "Nietzsche" },
  { quote: "The snake that cannot shed its skin must die.", by: "Nietzsche" },
  { quote: "I am not a man, I am dynamite.", by: "Nietzsche" },
  { quote: "All truly great thoughts are conceived while walking.", by: "Nietzsche" },
  { quote: "In heaven, all the interesting people are missing.", by: "Nietzsche" },
  { quote: "The best way to start the day: wonder if you can give someone joy.", by: "Nietzsche" },
  { quote: "Hope is the worst of evils: it prolongs torment.", by: "Nietzsche" },
  { quote: "That which is done out of love is beyond good and evil.", by: "Nietzsche" },
  { quote: "Is man God's mistake, or God man's?", by: "Nietzsche" },
  { quote: "Convictions are more dangerous enemies of truth than lies.", by: "Nietzsche" },
  { quote: "The individual has always had to struggle to keep from being overwhelmed by the tribe.", by: "Nietzsche" },
  { quote: "One must imagine Sisyphus happy.", by: "Camus" },
  { quote: "Hell is other people.", by: "Sartre" },
  { quote: "Life must be understood backward, but lived forward.", by: "Kierkegaard" },
  { quote: "Anxiety is the dizziness of freedom.", by: "Kierkegaard" },
  { quote: "Life swings like a pendulum between pain and boredom.", by: "Schopenhauer" },
  { quote: "Talent hits a target no one else can hit; genius one no one else can see.", by: "Schopenhauer" },
  { quote: "Dare to know!", by: "Kant" },
  { quote: "I am looking for an honest man.", by: "Diogenes" },
  { quote: "The obstacle is the way.", by: "Marcus Aurelius" },
  { quote: "We suffer more in imagination than in reality.", by: "Seneca" },
  { quote: "The unexamined life is not worth living.", by: "Socrates" },
  { quote: "Man is condemned to be free.", by: "Sartre" },
  { quote: "The owl of Minerva flies only at dusk.", by: "Hegel" },
  { quote: "Whereof one cannot speak, thereof one must be silent.", by: "Wittgenstein" },
  { quote: "Everything flows; nothing stands still.", by: "Heraclitus" },
  { quote: "Death is nothing to us.", by: "Epicurus" },
  { quote: "Those who know do not speak; those who speak do not know.", by: "Laozi" },
  { quote: "Real knowledge is knowing the extent of one's ignorance.", by: "Confucius" },
  { quote: "I dreamt I was a butterfly. Now I wonder if I am a butterfly dreaming I am a man.", by: "Zhuangzi" },
  { quote: "All that we are is the result of what we have thought.", by: "The Dhammapada" },
  { quote: "Knowing yourself is the beginning of all wisdom.", by: "Aristotle" },
  { quote: "Philosophy begins in wonder.", by: "Plato" },
  { quote: "No one is free who is not master of himself.", by: "Epictetus" },
  { quote: "Grant me chastity and continence, but not yet.", by: "Augustine" },
  { quote: "It is better to be feared than loved, if you cannot be both.", by: "Machiavelli" },
  { quote: "What do I know?", by: "Montaigne" },
  { quote: "I think, therefore I am.", by: "Descartes" },
  { quote: "The life of man: solitary, poor, nasty, brutish and short.", by: "Hobbes" },
  { quote: "All our troubles come from being unable to sit quietly alone in a room.", by: "Pascal" },
  { quote: "All things excellent are as difficult as they are rare.", by: "Spinoza" },
  { quote: "This is the best of all possible worlds.", by: "Leibniz" },
  { quote: "Reason is, and ought only to be, the slave of the passions.", by: "Hume" },
  { quote: "Man is born free, and everywhere he is in chains.", by: "Rousseau" },
  { quote: "Doubt is unpleasant, but certainty is absurd.", by: "Voltaire" },
  { quote: "A foolish consistency is the hobgoblin of little minds.", by: "Emerson" },
  { quote: "Most men lead lives of quiet desperation.", by: "Thoreau" },
  { quote: "The art of being wise is knowing what to overlook.", by: "William James" },
  { quote: "One is not born, but rather becomes, a woman.", by: "Simone de Beauvoir" },
  { quote: "Fools and fanatics are always so certain; the wise, so full of doubt.", by: "Bertrand Russell" },
]

const GENERAL = [
  "What's using the most memory on my PC right now?",
  "Find the biggest files in my Downloads folder and suggest what to delete",
  "Summarize the PDF I'm about to attach and list the action items",
  "Research the best value GPU under $500 right now, with sources",
  "Write a Python script that renames my photos by the date they were taken",
  "Make me a one-page cheat sheet on a topic I'm learning",
  "Plan a week of simple dinners and a shopping list",
  "Explain what's in my Documents folder and how it could be organised",
  "Check today's news on a topic I care about and summarise it with sources",
  "Turn my notes into a clean, well-structured document",
  "Build a small web page for a project idea and open it in the browser panel",
  "Compare two products I'm choosing between, with a table",
]

/** A few queries worth trying, shaped by what's on screen: the open chat's last turn, or the empty state. */
export function suggestFor(conv: Conv | null, n = 3): string[] {
  const pick = (xs: string[]) => [...xs].sort(() => Math.random() - 0.5).slice(0, n)
  if (!conv?.messages?.length) return pick(GENERAL)
  const msgs = conv.messages
  const lastAns = [...msgs].reverse().find((m) => m.role === "assistant" && !m.kind && m.content)
  const tools = msgs.slice(-30).filter((m) => m.kind === "tool").map((m) => m.tool?.name || "")
  const txt = (lastAns?.content || "").toLowerCase()
  const out: string[] = []
  if (msgs.some((m) => m.kind === "tool" && m.tool?.ok === false)) out.push("Look at what failed and try a different approach")
  if (/```/.test(lastAns?.content || "") || tools.some((t) => /write_file|edit_file/.test(t))) out.push("Write tests for this and run them", "Explain this code step by step")
  if (tools.some((t) => /web_search|web_fetch|browser/.test(t))) out.push("Compare the sources you used and flag any disagreements", "Give me the three most important points with links")
  if (/\|.*\|/.test(lastAns?.content || "")) out.push("Turn that table into a spreadsheet I can download")
  if (txt.length > 1200) out.push("Summarise that in five bullet points")
  if (msgs.some((m) => m.kind === "changes")) out.push("Show me everything you changed in this chat")
  out.push("What should I ask next to get the most out of this?")
  return [...new Set(out)].slice(0, n).concat(out.length < n ? pick(GENERAL).slice(0, n - out.length) : [])
}
