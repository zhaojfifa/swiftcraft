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
