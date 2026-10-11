import { clsx, type ClassValue } from "clsx"
import { extendTailwindMerge } from "tailwind-merge"

// teach tailwind-merge the design tokens (index.css @theme), or it reads `text-ui` as a colour and drops it
const twMerge = extendTailwindMerge({
  extend: {
    theme: {
      text: ["meta", "caption", "ui", "body", "chat", "title", "display"],
      radius: ["chip", "card", "panel", "modal"],
      shadow: ["elev-2", "elev-3"],
      ease: ["out", "spring"],
    },
  },
})

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
