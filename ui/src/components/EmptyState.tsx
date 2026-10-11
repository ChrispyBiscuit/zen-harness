import { SmokePlane } from "./SmokePlane"

export function Empty() {
  return (
    <div className="relative flex flex-1 flex-col items-center justify-center overflow-hidden px-6 pb-10">
      <div className="bg-grid pointer-events-none absolute inset-0" />
      <SmokePlane />
      <h1 className="sr-only">What should we work on?</h1>
    </div>
  )
}
