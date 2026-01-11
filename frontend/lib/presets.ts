export type ServiceName = 'swap';

export type SwapMode = 'baseline' | 'intelligent';

export const PRESETS: Record<ServiceName, Record<string, string>> = {
  swap: {
    baseline: 'presets/swap/baseline.mp4',
    intelligent: 'presets/swap/intelligent.mp4',
  },
};

// Later expansion (2 demos -> 4 demos):
// add new service keys here, e.g. avatar/enhance, and map modes to input_key

export function resolvePresetInputKey(service: string, mode: string): string {
  const key = `${service}:${mode}`;
  const raw = process.env.NEXT_PUBLIC_PRESET_MAP_JSON;
  if (raw) {
    try {
      const map = JSON.parse(raw) as Record<string, string>;
      if (map[key]) return map[key];
    } catch {
      // ignore invalid env JSON; fallback below
    }
  }
  return `presets/${service}/${mode}.mp4`;
}
