"""A typing-powered spiral galaxy, comets, and expanding stellar explosions."""

from collections import deque
import colorsys
import math

from aula import parse_color
from keyboard_profiles import WIRED_RGB


class Supernova:
    def __init__(self, layout, started=0):
        self.layout = layout
        self.last_time = started
        self.phase = 0
        self.speed = 2
        self.direction = 1
        self.energy = 0
        self.comets = deque(maxlen=32)
        self.blasts = deque(maxlen=4)
        xs = [position[1] for position in layout.values()]
        ys = [position[2] for position in layout.values()]
        self.center = (min(xs) + (max(xs) - min(xs)) * (8.2 / 17.5),
                       (min(ys) + max(ys)) / 2)

    def advance(self, now, speed=None):
        if speed is not None:
            self.speed = speed
        elapsed = max(0, now - self.last_time)
        self.energy = max(0, self.energy - elapsed * 0.08)
        self.phase += elapsed * (0.35 + self.speed * 0.22) * self.direction
        self.last_time = max(self.last_time, now)
        while self.comets and now - self.comets[0][0] > 4:
            self.comets.popleft()
        while self.blasts and now - self.blasts[0] > 2:
            self.blasts.popleft()

    def press(self, name, now):
        position = self.layout.get(name)
        if position is None:
            return
        self.advance(now)
        index, x, y = position
        if name == "BACKSPACE":
            self.direction *= -1
        self.comets.append((now, x, y, index * 0.037))
        self.energy = min(1.2, self.energy + 0.09)
        if name == "SPACE" or self.energy >= 1:
            self.blasts.append(now)
            self.energy = 0.12

    def particles(self, now, speed):
        particles = []
        center_x, center_y = self.center
        for born, x, y, tint in self.comets:
            age = max(0, now - born)
            duration = 2.4 / (0.7 + speed * 0.3)
            distance = math.hypot(x - center_x, (y - center_y) * 1.6)
            angle = math.atan2((y - center_y) * 1.6, x - center_x)
            for segment in range(3):
                progress = max(0, (age - segment * 0.1) / duration)
                if progress >= 1:
                    continue
                radius = distance * (1 - progress) ** 1.35
                spin = angle + self.direction * progress * math.tau
                particles.append((center_x + math.cos(spin) * radius,
                                  center_y + math.sin(spin) * radius / 1.6,
                                  tint, (1 - segment * 0.28) * (1 - progress)))
        return particles

    def render(self, now, color, brightness, speed):
        rgb = parse_color(color)
        if not 0 <= brightness <= 4 or not 0 <= speed <= 4:
            raise ValueError("Brightness and speed must be between 0 and 4.")
        self.advance(now, speed)
        hue, _, _ = colorsys.rgb_to_hsv(*(channel / 255 for channel in rgb))
        particles = self.particles(now, speed)
        colors = {}
        center_x, center_y = self.center
        for index, x, y in self.layout.values():
            dx, dy = x - center_x, (y - center_y) * 1.6
            radius = math.hypot(dx, dy)
            angle = math.atan2(dy, dx)
            spiral = (0.5 + 0.5 * math.cos(3 * angle - self.phase * 2 + radius * 0.85)) ** 9
            glow = (0.06 + 0.5 * spiral) * math.exp(-radius * 0.07)
            twinkle = max(0, math.sin(self.phase * 1.1 + index * 2.399)) ** 24 * 0.3
            channels = list(colorsys.hsv_to_rgb((hue + radius * 0.018 + self.phase * 0.025) % 1,
                                               0.85, glow + twinkle))
            core = math.exp(-((radius - 1.15 - self.energy * 0.4) / 0.8) ** 2)
            core *= 0.25 + self.energy * 0.6
            for channel, value in enumerate((1, 0.5, 0.12)):
                channels[channel] += core * value
            if radius < 0.55 + self.energy * 0.35:
                channels = [value * 0.08 for value in channels]
            for particle_x, particle_y, tint, strength in particles:
                distance = ((x - particle_x) / 0.8) ** 2 + ((y - particle_y) / 0.65) ** 2
                if distance > 14:
                    continue
                light = math.exp(-distance) * strength
                comet_color = colorsys.hsv_to_rgb((hue + tint) % 1, 0.5, 1)
                for channel in range(3):
                    channels[channel] += light * comet_color[channel]
            for born in self.blasts:
                age = max(0, now - born)
                ring = math.exp(-((radius - age * (9 + speed * 1.5)) / 1.1) ** 2)
                ring *= math.exp(-age * 0.6)
                afterglow = math.exp(-radius * 0.2 - age * 4) * 0.4
                blast_color = colorsys.hsv_to_rgb((hue + 0.5 + age * 0.28) % 1, 0.45, 1)
                for channel in range(3):
                    channels[channel] += (ring + afterglow) * blast_color[channel]
            colors[index] = bytes(
                round(min(1, max(0, value)) * brightness / 4 * 255) for value in channels)
        return colors

    def frame(self, now, color, brightness, speed):
        return WIRED_RGB.encode(self.render(now, color, brightness, speed))
