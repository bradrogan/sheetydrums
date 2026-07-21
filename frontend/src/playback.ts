// Thin wrapper around the YouTube IFrame Player API. The API has no time-update
// event, so we poll getCurrentTime() on a requestAnimationFrame loop while
// playing and push ticks to a listener. Kept dependency-free (no @types/youtube)
// via a minimal ambient declaration.

interface YTPlayer {
  playVideo(): void;
  pauseVideo(): void;
  seekTo(seconds: number, allowSeekAhead: boolean): void;
  getCurrentTime(): number;
  getDuration(): number;
  setVolume(volume: number): void;
  getPlayerState(): number;
  destroy(): void;
}

interface YTPlayerEvent {
  data: number;
}

declare global {
  interface Window {
    YT?: {
      Player: new (
        el: HTMLElement | string,
        opts: {
          videoId: string;
          playerVars?: Record<string, number | string>;
          events?: {
            onReady?: () => void;
            onStateChange?: (e: YTPlayerEvent) => void;
          };
        },
      ) => YTPlayer;
      PlayerState: { PLAYING: number; PAUSED: number; ENDED: number };
    };
    onYouTubeIframeAPIReady?: () => void;
  }
}

/** Clamp a seek target to [0, duration]; duration may be 0/NaN before ready. */
export function clampSeek(seconds: number, duration: number): number {
  const lo = 0;
  const hi = Number.isFinite(duration) && duration > 0 ? duration : Infinity;
  return Math.max(lo, Math.min(hi, seconds));
}

const API_SRC = 'https://www.youtube.com/iframe_api';
let apiReady: Promise<void> | null = null;

function loadApi(): Promise<void> {
  if (apiReady) return apiReady;
  apiReady = new Promise<void>((resolve) => {
    if (window.YT?.Player) {
      resolve();
      return;
    }
    // The API calls this global when it finishes loading.
    window.onYouTubeIframeAPIReady = () => resolve();
    const script = document.createElement('script');
    script.src = API_SRC;
    document.head.appendChild(script);
  });
  return apiReady;
}

export interface PlayerHandle {
  play(): void;
  pause(): void;
  toggle(): void;
  seekTo(seconds: number): void;
  /** Seek by a relative delta (seconds), clamped to [0, duration]. */
  seekBy(delta: number): void;
  getCurrentTime(): number;
  getDuration(): number;
  /** Set volume as a percentage 0..100. */
  setVolume(pct: number): void;
  isPlaying(): boolean;
  /** Fires ~every animation frame while playing, plus once on pause/seek. */
  onTick(cb: (seconds: number) => void): void;
  /** Fires when play/pause state flips. */
  onStateChange(cb: (playing: boolean) => void): void;
  destroy(): void;
}

export async function createYouTubePlayer(
  container: HTMLElement,
  videoId: string,
): Promise<PlayerHandle> {
  await loadApi();
  const YT = window.YT!;

  // YT replaces the target element with an iframe; give it a fresh child so the
  // stable #player container survives project navigation.
  container.innerHTML = '';
  const mount = document.createElement('div');
  container.appendChild(mount);

  let tickCb: (t: number) => void = () => {};
  let stateCb: (playing: boolean) => void = () => {};
  let rafId = 0;

  const player: YTPlayer = await new Promise<YTPlayer>((resolve) => {
    const p = new YT.Player(mount, {
      videoId,
      playerVars: { rel: 0, modestbranding: 1 },
      events: {
        onReady: () => resolve(p),
        onStateChange: (e) => {
          const playing = e.data === YT.PlayerState.PLAYING;
          stateCb(playing);
          if (playing) startLoop();
          else {
            stopLoop();
            tickCb(p.getCurrentTime()); // settle the playhead on pause/seek/end
          }
        },
      },
    });
  });

  function startLoop(): void {
    if (rafId) return;
    const loop = (): void => {
      tickCb(player.getCurrentTime());
      rafId = requestAnimationFrame(loop);
    };
    rafId = requestAnimationFrame(loop);
  }
  function stopLoop(): void {
    if (rafId) cancelAnimationFrame(rafId);
    rafId = 0;
  }

  return {
    play: () => player.playVideo(),
    pause: () => player.pauseVideo(),
    toggle: () =>
      player.getPlayerState() === YT.PlayerState.PLAYING
        ? player.pauseVideo()
        : player.playVideo(),
    seekTo: (seconds) => {
      const clamped = clampSeek(seconds, player.getDuration());
      player.seekTo(clamped, true);
      tickCb(clamped);
    },
    seekBy: (delta) => {
      const clamped = clampSeek(player.getCurrentTime() + delta, player.getDuration());
      player.seekTo(clamped, true);
      tickCb(clamped);
    },
    getCurrentTime: () => player.getCurrentTime(),
    getDuration: () => player.getDuration(),
    setVolume: (pct) => player.setVolume(Math.max(0, Math.min(100, pct))),
    isPlaying: () => player.getPlayerState() === YT.PlayerState.PLAYING,
    onTick: (cb) => {
      tickCb = cb;
    },
    onStateChange: (cb) => {
      stateCb = cb;
    },
    destroy: () => {
      stopLoop();
      player.destroy();
    },
  };
}
