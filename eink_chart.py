# Generate a chart image of electricity spot prices for display on an e-ink screen.

import asyncio
import logging
from decimal import Decimal
from zoneinfo import ZoneInfo
from PIL import Image, ImageDraw, ImageFont
import datetime

from custom_components.cz_energy_spot_prices.spot_rate import SpotRate

# Configure logging
logger = logging.getLogger(__name__)

# Timezone configuration
TIMEZONE = ZoneInfo('Europe/Prague')

# Image dimensions
WIDTH = 480  # Image width in pixels
HEIGHT = 800  # Image height in pixels
GRAPH_HEIGHT = 180  # Graph height in pixels

# Margins and spacing
MARGIN_LEFT = 10
MARGIN_TOP = 30
MARGIN_RIGHT = 10
MARGIN_BOTTOM = 20
GRAPH_HEADER = 15  # Space for header text with current hour
BAR_GAP = 2  # Gap between bars in pixels
VERTICAL_LINE_SPACING = 5  # Spacing between vertical line segments
HOUR_MARKER_OFFSET = 5  # Offset for hour marker text
TEXT_PADDING = 2  # Padding for text on bars

# E-ink display colors
BACKGROUND_COLOR = (255, 255, 255)  # White
TEXT_COLOR = (0, 0, 0)  # Black
GRID_COLOR = (200, 200, 200)  # Light gray for grid lines
CURRENT_HOUR_MARKER_COLOR = (0, 0, 0)  # Black
LOW_PRICE_COLOR = (0, 0, 0)  # Black for low prices
HIGH_PRICE_COLOR = (255, 0, 0)  # Red for high prices

# Price threshold for color change (CZK/kWh)
PRICE_THRESHOLD = 2.5  # Above this is red, below is black

# Text image dimensions
TEXT_IMG_HEIGHT = 20
# TEXT_IMG_WIDTH is now calculated dynamically based on text length

# Soft maximum Y-axis value (CZK/kWh)
SOFT_PRICE_RANGE = Decimal(5)

# Output file path
OUTPUT_FILE = "spot_prices.png"

# Font configuration
FONT_NAME = "FiraMono-Regular.ttf"
FONT_SIZE = 13


type Font = ImageFont.FreeTypeFont | ImageFont.ImageFont

def load_font() -> Font:
    """Load font for the chart. Falls back to default if specified font is not available."""
    try:
        return  ImageFont.truetype(FONT_NAME, FONT_SIZE)  # Large font for header
    except IOError:
        logger.warning(f"Font {FONT_NAME} not found, using default font")
        return ImageFont.load_default()


def draw_vertical_marker(draw: ImageDraw.ImageDraw, label: str, x: float, y: float,
                         height: float, font: Font) -> None:
    """Draw vertical grid lines and hour markers."""
    # Draw dashed vertical line
    i = y
    while i < height:
        draw.line((x, i, x, i + 1), fill=TEXT_COLOR, width=1)
        i += VERTICAL_LINE_SPACING

    # Draw hour marker text
    draw.text(
        (float(x + HOUR_MARKER_OFFSET), float(y)),
        label,
        font=font,
        fill=TEXT_COLOR
    )


def get_bar_color(price: Decimal) -> tuple[int, int, int]:
    """Determine bar color based on price threshold."""
    price_float = float(price)
    return LOW_PRICE_COLOR if price_float < PRICE_THRESHOLD else HIGH_PRICE_COLOR


def draw_price_text(img: Image.Image, price: Decimal, x: float, bar_width: float,
                     bar_top: float, bar_height: float, font: Font) -> None:
    """Draw rotated price text on or above the bar."""
    price_text = f"{price:.2f}"

    # Calculate the text width first
    dummy_draw = ImageDraw.Draw(Image.new('RGBA', (1, 1)))
    text_width = dummy_draw.textlength(price_text, font=font)

    # Add padding to ensure text fits
    text_img_width = int(
        text_width + TEXT_PADDING
    )

    # Create a new transparent image for the text with dynamic width
    text_img = Image.new('RGBA', (text_img_width, TEXT_IMG_HEIGHT), (255, 255, 255, 0))
    text_draw = ImageDraw.Draw(text_img)

    # Center the text in the image
    text_x = (text_img_width - text_width) / 2
    text_y = 0

    # Draw the text
    text_draw.text(
        (text_x, text_y),
        price_text,
        font=font,
        fill=TEXT_COLOR
    )

    # Rotate the text image by 90 degrees
    rotated_text = text_img.rotate(90, expand=True, center=(text_img_width // 2, TEXT_IMG_HEIGHT // 2))

    # Position the rotated text on or above the bar
    paste_x = int(x + (bar_width / 2) - (rotated_text.width / 2))

    # For very short bars, place text above the bar
    if bar_height < text_width + TEXT_PADDING:
        paste_y = int(bar_top - rotated_text.height)
    else:
        # Place text near the top of the bar
        paste_y = int(bar_top + TEXT_PADDING)

    # Paste the rotated text onto the main image
    img.paste(rotated_text, (paste_x + TEXT_PADDING, paste_y), rotated_text)


def draw_bar(draw: ImageDraw.ImageDraw, img: Image.Image, hour: datetime.datetime,
             price: Decimal, bar_unit_height: float, x: float, bar_width: float, graph_y: float,
             graph_height: float, font_small: Font) -> None:
    """Draw a price bar with its label."""
    # Calculate bar height based on price
    bar_height = float(price) * bar_unit_height

    # Calculate bar coordinates
    bar_top = graph_y + graph_height - bar_height
    bar_bottom = graph_y + graph_height

    if bar_top > bar_bottom:
        bar_top, bar_bottom = bar_bottom, bar_top  # Swap if necessary

    # Draw vertical lines and hour markers every 4 hours
    if hour.hour % 4 == 0:
        draw_vertical_marker(draw, f"{hour.hour:02d}:00", x, graph_y, bar_top, font_small)

    # Get color based on price
    bar_color = get_bar_color(price)

    # Draw the bar
    if abs(bar_bottom - bar_top) < 1:
        # Draw a line for very small bars
        draw.line(
            (float(x), float(bar_top), float(x + bar_width), float(bar_bottom)),
            fill=bar_color,
            width=1
        )
    else:
        # Draw a rectangle for normal bars
        draw.rectangle(
            (float(x), float(bar_top), float(x + bar_width), float(bar_bottom)),
            fill=None,
            outline=bar_color,
            width=1
        )

    # Draw the price text
    draw_price_text(img, price, x, bar_width, bar_top, bar_height, font_small)


def draw_chart(prices: dict[datetime.datetime, Decimal]) -> None:
    """Generate a chart image of electricity spot prices."""
    # Load fonts
    font = load_font()

    # Get current time and filter prices for today only
    now_utc = datetime.datetime.now(tz=datetime.timezone.utc)
    start_hour = now_utc.replace(minute=0, second=0, microsecond=0).astimezone(TIMEZONE)

    # Filter prices for today only
    today_prices: dict[datetime.datetime, Decimal] = {}
    max_price = 0
    min_price = 0
    for time, price in prices.items():
        if time < start_hour:
            # Skip past hours
            continue

        today_prices[time.astimezone(TIMEZONE)] = price

        if price > max_price:
            max_price = price
        if price < min_price:
            min_price = price

    if not today_prices:
        logger.error("No price data available for today")
        return

    # Get current hour price
    current_hour = now_utc.replace(minute=0, second=0, microsecond=0)
    current_price = prices.get(current_hour, Decimal(0))

    # Create image
    img = Image.new("RGB", (WIDTH, HEIGHT), color=BACKGROUND_COLOR)
    draw = ImageDraw.Draw(img)

    # Draw header with current price
    draw.text(
        (MARGIN_LEFT, MARGIN_LEFT),
        f"Aktuální cena ({current_hour.astimezone(TIMEZONE).hour:02d}:00): {current_price:.2f} Kč/kWh",
        font=font,
        fill=TEXT_COLOR,
    )

    # Set graph dimensions
    graph_x = MARGIN_LEFT
    graph_y = MARGIN_TOP
    graph_width = WIDTH - MARGIN_LEFT - MARGIN_RIGHT
    graph_height = GRAPH_HEIGHT - MARGIN_BOTTOM - MARGIN_TOP
    bar_width = (graph_width / len(today_prices)) - BAR_GAP if today_prices else 0
    price_range = max_price - min_price

    # Ensure a minimum price range for better visualization
    if price_range < SOFT_PRICE_RANGE:
        price_range = SOFT_PRICE_RANGE

    bar_unit_height = (graph_height - GRAPH_HEADER) / float(price_range) if price_range > 0 else 0

    # Draw bars for each hour
    x = graph_x
    for hour, price in today_prices.items():
        draw_bar(draw, img, hour, price, bar_unit_height, x, bar_width, graph_y, graph_height, font)
        x += bar_width + BAR_GAP  # Move to next bar position with gap

    # Save image
    img.save(OUTPUT_FILE)
    logger.info(f"Image '{OUTPUT_FILE}' has been generated for e-ink display")


async def generate_chart() -> None:
    """Generate a chart with current electricity spot prices."""
    now_utc = datetime.datetime.now(tz=datetime.timezone.utc)

    # Initialize SpotRate only when needed
    spot_rate = SpotRate()

    try:
        prices = await spot_rate.get_electricity_rates(now_utc, in_eur=False, unit="kWh")
        draw_chart(prices)
    except Exception as e:
        logger.exception(f"Error generating chart: {e}")


if __name__ == "__main__":
    asyncio.run(generate_chart())
