// Type shim for the npm `asciinema-player` package. The package ships
// untyped JS, so we declare the surface area we actually call from
// `src/components/AsciinemaPlayer.tsx`.

declare module "asciinema-player" {
  export interface AsciinemaPlayerOptions {
    autoPlay?: boolean;
    loop?: boolean;
    cols?: number;
    rows?: number;
    idleTimeLimit?: number;
    speed?: number;
    poster?: string;
    theme?: string;
    fit?: string | false;
  }

  export interface AsciinemaPlayerInstance {
    dispose?: () => void;
    play?: () => void;
    pause?: () => void;
  }

  export function create(
    castUrl: string,
    target: HTMLElement,
    options?: AsciinemaPlayerOptions,
  ): AsciinemaPlayerInstance;
}

declare module "asciinema-player/dist/bundle/asciinema-player.css" {
  const css: string;
  export default css;
}
