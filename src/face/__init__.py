"""TUI animated face using braille characters — the Viki dot-cloud rendered in terminal."""

import math
import time
import os
import sys

# Braille character base: U+2800, each char is a 2x4 dot grid
# Dots numbered: 1 4
#                2 5
#                3 6
#                7 8

BRAILLE_BASE = 0x2800

def dots_to_braille(*dots):
    """Convert dot positions (1-8) to a braille character."""
    offset = 0
    for d in dots:
        offset |= (1 << [0,1,2,6,3,4,5,7][d-1])
    return chr(BRAILLE_BASE + offset)


# Simplified face mesh — key landmark positions (normalised 0-1)
# Based on MediaPipe face mesh topology, simplified to ~80 key points
FACE_POINTS = [
    # Jaw outline (0-16)
    (0.15, 0.45), (0.13, 0.55), (0.14, 0.65), (0.16, 0.74), (0.20, 0.82),
    (0.26, 0.88), (0.33, 0.93), (0.42, 0.96), (0.50, 0.97),
    (0.58, 0.96), (0.67, 0.93), (0.74, 0.88), (0.80, 0.82),
    (0.84, 0.74), (0.86, 0.65), (0.87, 0.55), (0.85, 0.45),
    # Left eyebrow (17-21)
    (0.24, 0.32), (0.28, 0.28), (0.34, 0.27), (0.39, 0.28), (0.43, 0.31),
    # Right eyebrow (22-26)
    (0.57, 0.31), (0.61, 0.28), (0.66, 0.27), (0.72, 0.28), (0.76, 0.32),
    # Left eye (27-32)
    (0.28, 0.38), (0.31, 0.36), (0.35, 0.36), (0.39, 0.38),
    (0.35, 0.40), (0.31, 0.40),
    # Right eye (33-38)
    (0.61, 0.38), (0.65, 0.36), (0.69, 0.36), (0.72, 0.38),
    (0.69, 0.40), (0.65, 0.40),
    # Nose (39-47)
    (0.50, 0.38), (0.50, 0.44), (0.50, 0.50), (0.50, 0.56),
    (0.42, 0.58), (0.45, 0.60), (0.50, 0.61), (0.55, 0.60), (0.58, 0.58),
    # Mouth outer (48-59)
    (0.36, 0.70), (0.40, 0.67), (0.44, 0.65), (0.50, 0.66),
    (0.56, 0.65), (0.60, 0.67), (0.64, 0.70),
    (0.60, 0.75), (0.56, 0.77), (0.50, 0.78), (0.44, 0.77), (0.40, 0.75),
]


class TUIFace:
    """Renders an animated dot-cloud face in the terminal using braille characters."""

    def __init__(self, width=60, height=30):
        self.width = width  # in characters
        self.height = height  # in characters
        self.t = 0.0
        self.state = "idle"  # idle, talking, thinking, listening
        self.talk_phase = 0.0
        self.blink_timer = 0.0
        self.blink_duration = 0.15
        self.is_blinking = False

    def set_state(self, state):
        self.state = state

    def _apply_animation(self, points, t):
        """Apply animation based on current state."""
        animated = []
        for i, (x, y) in enumerate(points):
            # Idle breathing
            breath = math.sin(t * 1.5) * 0.005
            y += breath

            # Subtle float
            float_x = math.sin(t * 0.7 + i * 0.1) * 0.003
            float_y = math.cos(t * 0.5 + i * 0.15) * 0.002
            x += float_x
            y += float_y

            # Blinking (eyes: indices 27-38)
            if 27 <= i <= 38 and self.is_blinking:
                # Collapse eye points vertically toward center
                eye_center_y = 0.38
                y = eye_center_y + (y - eye_center_y) * 0.1

            # Talking (mouth: indices 48-59)
            if 48 <= i <= 59 and self.state == "talking":
                mouth_open = math.sin(t * 8) * 0.03 + 0.02
                if i >= 55:  # lower lip
                    y += mouth_open
                else:  # upper lip
                    y -= mouth_open * 0.3

            # Thinking (slight head tilt + eyebrow raise)
            if self.state == "thinking":
                if 17 <= i <= 26:  # eyebrows
                    y -= 0.015 + math.sin(t * 2) * 0.005
                # Slight rotation
                cx, cy = 0.5, 0.5
                angle = math.sin(t * 0.8) * 0.03
                rx = cx + (x - cx) * math.cos(angle) - (y - cy) * math.sin(angle)
                ry = cy + (x - cx) * math.sin(angle) + (y - cy) * math.cos(angle)
                x, y = rx, ry

            animated.append((x, y))
        return animated

    def render(self, dt=0.05):
        """Render one frame as scattered particle cloud (Viki style)."""
        self.t += dt

        # Blink logic
        self.blink_timer -= dt
        if self.blink_timer <= 0:
            if self.is_blinking:
                self.is_blinking = False
                self.blink_timer = 2.5 + math.sin(self.t) * 1.5
            else:
                self.is_blinking = True
                self.blink_timer = self.blink_duration

        # Animate points
        points = self._apply_animation(FACE_POINTS, self.t)

        # Generate scattered particles around each landmark
        import random
        random.seed(42)  # deterministic scatter pattern
        particles = []
        for i, (x, y) in enumerate(points):
            # More particles near eyes and mouth for definition
            density = 3
            if 27 <= i <= 38:  # eyes
                density = 5
            elif 48 <= i <= 59:  # mouth
                density = 4
            elif 17 <= i <= 26:  # eyebrows
                density = 4

            for _ in range(density):
                # Scatter with slight animation
                scatter_x = (random.random() - 0.5) * 0.04
                scatter_y = (random.random() - 0.5) * 0.04
                # Add time-based drift
                drift_x = math.sin(self.t * 0.3 + i * 0.7 + random.random()) * 0.008
                drift_y = math.cos(self.t * 0.4 + i * 0.5 + random.random()) * 0.008
                px = x + scatter_x + drift_x
                py = y + scatter_y + drift_y
                particles.append((px, py))

        # Render to braille grid — single dots, not clusters
        px_w = self.width * 2
        px_h = self.height * 4
        grid = [[False] * px_w for _ in range(px_h)]

        for (x, y) in particles:
            px = int(x * px_w)
            py = int(y * px_h)
            if 0 <= px < px_w and 0 <= py < px_h:
                grid[py][px] = True

        # Convert grid to braille characters
        lines = []
        for row in range(0, px_h, 4):
            line = ""
            for col in range(0, px_w, 2):
                val = 0
                # Braille dot mapping: positions in 2x4 cell
                mapping = [(0,0,0x01), (1,0,0x02), (2,0,0x04), (0,1,0x08),
                           (1,1,0x10), (2,1,0x20), (3,0,0x40), (3,1,0x80)]
                for dy, dx, bit in mapping:
                    if row + dy < px_h and col + dx < px_w:
                        if grid[row + dy][col + dx]:
                            val |= bit
                line += chr(BRAILLE_BASE + val) if val else " "
            lines.append(line)

        return lines


def demo():
    """Run standalone demo of the TUI face."""
    face = TUIFace(width=40, height=20)
    states = ["idle", "talking", "thinking", "idle"]
    state_idx = 0
    state_timer = 0

    try:
        print("\033[?25l", end="")  # hide cursor
        print("\033[2J", end="")    # clear screen
        while True:
            frame = face.render(dt=0.08)
            print("\033[H", end="")  # move to top-left
            print(f"\033[36m", end="")  # cyan
            for line in frame:
                print(f"  {line}")
            print(f"\033[0m", end="")
            print(f"\n  State: {face.state}  (cycles automatically)")
            sys.stdout.flush()
            time.sleep(0.08)

            state_timer += 0.08
            if state_timer > 3:
                state_timer = 0
                state_idx = (state_idx + 1) % len(states)
                face.set_state(states[state_idx])
    except KeyboardInterrupt:
        print("\033[?25h")  # show cursor


if __name__ == "__main__":
    demo()
