import pygame
import sys
import json
import os
import time
import numpy as np
import pyautogui
import cv2

_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Design tokens (match Flask app) ───────────────────────────────────────────
WHITE      = (255, 255, 255)
PRIMARY    = (37,  99,  235)   # #2563eb
TEXT_DARK  = (26,  26,  26)    # #1a1a1a
MUTED      = (107, 114, 128)   # #6b7280


def _rounded_surface(w, h, color_rgba, radius=8):
    """SRCALPHA surface with a rounded rect — used for semi-transparent overlays."""
    surf = pygame.Surface((w, h), pygame.SRCALPHA)
    pygame.draw.rect(surf, color_rgba, (0, 0, w, h), border_radius=radius)
    return surf


def run_selector(initial_left=None, initial_top=None,
                 initial_width=425, initial_height=700,
                 delay=3.5):
    """
    delay: seconds to wait before taking the screenshot.
    The caller (Flask) shows a matching countdown in the browser so the user
    has time to switch to their video before the screenshot fires.
    """
    # Get screen size via pyautogui — no pygame needed yet
    screen_w, screen_h = pyautogui.size()

    if initial_left is None:
        initial_left = (screen_w - initial_width) // 2
    if initial_top is None:
        initial_top = (screen_h - initial_height) // 2

    # ── Wait for user to switch to their video ────────────────────────────────
    time.sleep(delay)

    # ── Screenshot (pygame window is not open — clean capture) ───────────────
    shot = pyautogui.screenshot()
    shot_np = np.array(shot)[:, :, :3]
    shot_resized = cv2.resize(shot_np, (screen_w, screen_h),
                              interpolation=cv2.INTER_AREA)

    # ── Phase 2: fullscreen selector overlay ─────────────────────────────────
    pygame.init()
    os.environ['SDL_VIDEO_WINDOW_POS'] = '0,0'
    screen = pygame.display.set_mode(
        (screen_w, screen_h), pygame.NOFRAME | pygame.SCALED
    )
    pygame.display.set_caption('PlatformPose — Set Capture Region')

    # Force this window to the front on macOS
    if sys.platform == 'darwin':
        import subprocess as _sp
        _sp.Popen(
            ['osascript', '-e',
             f'tell application "System Events" to set frontmost of '
             f'(first process whose unix id is {os.getpid()}) to true'],
            stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
        )

    bg_surface = pygame.image.frombuffer(
        shot_resized.tobytes(), (screen_w, screen_h), 'RGB'
    )

    HANDLE_SZ  = 12
    SEL_COLOR  = PRIMARY
    DIM_RGBA   = (*PRIMARY, 210)
    DONE_COLOR = PRIMARY

    rect       = pygame.Rect(initial_left, initial_top, initial_width, initial_height)
    dragging   = False
    resizing   = None
    drag_off   = (0, 0)
    drag_start = (0, 0)
    rect_start = None

    # Helvetica Neue is available on this system and renders more cleanly
    # than plain Helvetica at screen sizes
    font_dim  = pygame.font.SysFont('Helvetica Neue', 13)
    font_inst = pygame.font.SysFont('Helvetica Neue', 12)
    font_done = pygame.font.SysFont('Helvetica Neue', 15, bold=True)

    INST_TEXT = 'Drag to move  ·  Drag corners to resize  ·  ESC to cancel'
    inst_surf = font_inst.render(INST_TEXT, True, WHITE)
    inst_bar_w = inst_surf.get_width() + 28
    inst_bar_h = 28
    inst_bar_x = screen_w // 2 - inst_bar_w // 2
    inst_bar_y = 14

    done_btn = pygame.Rect(screen_w // 2 - 72, screen_h - 64, 144, 42)
    clock    = pygame.time.Clock()
    running  = True

    def get_handles():
        h = HANDLE_SZ // 2
        return {
            'tl': pygame.Rect(rect.left  - h, rect.top    - h, HANDLE_SZ, HANDLE_SZ),
            'tr': pygame.Rect(rect.right - h, rect.top    - h, HANDLE_SZ, HANDLE_SZ),
            'bl': pygame.Rect(rect.left  - h, rect.bottom - h, HANDLE_SZ, HANDLE_SZ),
            'br': pygame.Rect(rect.right - h, rect.bottom - h, HANDLE_SZ, HANDLE_SZ),
        }

    while running:
        # Background screenshot
        screen.blit(bg_surface, (0, 0))

        # Dark overlay with selection cut-out (the video region stays bright)
        overlay = pygame.Surface((screen_w, screen_h), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 155))
        pygame.draw.rect(overlay, (0, 0, 0, 0), rect)
        screen.blit(overlay, (0, 0))

        # Selection border
        pygame.draw.rect(screen, SEL_COLOR, rect, 2, border_radius=2)

        # Corner handles
        for handle in get_handles().values():
            pygame.draw.rect(screen, SEL_COLOR, handle, border_radius=3)
            pygame.draw.rect(screen, WHITE, handle, 1, border_radius=3)

        # Dimension badge (blue pill above selection)
        dim_text = f'{rect.width} × {rect.height}   {rect.left}, {rect.top}'
        dim_surf = font_dim.render(dim_text, True, WHITE)
        badge_w  = dim_surf.get_width() + 20
        badge_h  = dim_surf.get_height() + 8
        badge_x  = rect.left
        badge_y  = max(4, rect.top - badge_h - 8)
        badge    = _rounded_surface(badge_w, badge_h, DIM_RGBA, radius=badge_h // 2)
        screen.blit(badge, (badge_x, badge_y))
        screen.blit(dim_surf, (badge_x + 10, badge_y + 4))

        # Instruction bar — top-center dark pill
        bar = _rounded_surface(inst_bar_w, inst_bar_h, (0, 0, 0, 185), radius=14)
        screen.blit(bar, (inst_bar_x, inst_bar_y))
        screen.blit(inst_surf, (
            inst_bar_x + 14,
            inst_bar_y + (inst_bar_h - inst_surf.get_height()) // 2,
        ))

        # Done button
        done_bg = _rounded_surface(done_btn.width, done_btn.height,
                                   (*DONE_COLOR, 255), radius=8)
        screen.blit(done_bg, done_btn.topleft)
        done_lbl = font_done.render('✓  Done', True, WHITE)
        screen.blit(done_lbl, (
            done_btn.centerx - done_lbl.get_width() // 2,
            done_btn.centery - done_lbl.get_height() // 2,
        ))

        pygame.display.flip()

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                sys.exit()

            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    pygame.quit()
                    sys.exit()

            elif event.type == pygame.MOUSEBUTTONDOWN:
                mx, my = event.pos

                if done_btn.collidepoint(mx, my):
                    with open(os.path.join(_DIR, 'region_result.json'), 'w') as f:
                        json.dump({
                            'left':   rect.left,
                            'top':    rect.top,
                            'width':  rect.width,
                            'height': rect.height,
                        }, f)
                    running = False
                    break

                handles = get_handles()
                for name, handle in handles.items():
                    if handle.collidepoint(mx, my):
                        resizing   = name
                        drag_start = (mx, my)
                        rect_start = pygame.Rect(rect)
                        break
                else:
                    if rect.collidepoint(mx, my):
                        dragging = True
                        drag_off = (mx - rect.left, my - rect.top)

            elif event.type == pygame.MOUSEBUTTONUP:
                dragging   = False
                resizing   = None
                rect_start = None

            elif event.type == pygame.MOUSEMOTION:
                mx, my = event.pos
                dx = mx - drag_start[0]
                dy = my - drag_start[1]

                if dragging:
                    rect.left = mx - drag_off[0]
                    rect.top  = my - drag_off[1]
                elif resizing and rect_start:
                    if resizing == 'tl':
                        rect.left   = rect_start.left  + dx
                        rect.top    = rect_start.top   + dy
                        rect.width  = max(50, rect_start.width  - dx)
                        rect.height = max(50, rect_start.height - dy)
                    elif resizing == 'tr':
                        rect.top    = rect_start.top   + dy
                        rect.width  = max(50, rect_start.width  + dx)
                        rect.height = max(50, rect_start.height - dy)
                    elif resizing == 'bl':
                        rect.left   = rect_start.left  + dx
                        rect.width  = max(50, rect_start.width  - dx)
                        rect.height = max(50, rect_start.height + dy)
                    elif resizing == 'br':
                        rect.width  = max(50, rect_start.width  + dx)
                        rect.height = max(50, rect_start.height + dy)

        clock.tick(60)

    pygame.quit()


if __name__ == '__main__':
    delay = 3.5
    if '--delay' in sys.argv:
        idx   = sys.argv.index('--delay')
        delay = float(sys.argv[idx + 1])
    if len(sys.argv) > 1 and not sys.argv[1].startswith('--'):
        with open(sys.argv[1]) as f:
            coords = json.load(f)
        run_selector(
            coords.get('left'),
            coords.get('top'),
            coords.get('width',  425),
            coords.get('height', 700),
            delay=delay,
        )
    else:
        run_selector(delay=delay)
