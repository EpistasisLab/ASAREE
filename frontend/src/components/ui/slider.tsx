import { Slider as SliderPrimitive } from "@base-ui/react/slider"

import { cn } from "@/lib/utils"

function Slider({
  className,
  ...props
}: SliderPrimitive.Root.Props) {
  return (
    <SliderPrimitive.Root data-slot="slider" className={cn("relative flex items-center", className)} {...props}>
      <SliderPrimitive.Control data-slot="slider-control" className="flex w-full touch-none items-center py-2 select-none">
        <SliderPrimitive.Track
          data-slot="slider-track"
          className="relative h-1 w-full grow rounded-full bg-input select-none"
        >
          <SliderPrimitive.Indicator data-slot="slider-indicator" className="absolute h-full rounded-full bg-primary select-none" />
          <SliderPrimitive.Thumb
            data-slot="slider-thumb"
            className="block size-3 shrink-0 rounded-full bg-primary shadow-[0_0_10px_-2px_var(--primary)] outline-none transition-shadow select-none has-[:focus-visible]:shadow-[0_0_14px_-1px_var(--primary)] data-dragging:shadow-[0_0_14px_-1px_var(--primary)]"
          />
        </SliderPrimitive.Track>
      </SliderPrimitive.Control>
    </SliderPrimitive.Root>
  )
}

export { Slider }
