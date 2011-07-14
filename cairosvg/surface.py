# -*- coding: utf-8 -*-
# This file is part of CairoSVG
# Copyright © 2010-2011 Kozea
#
# This library is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with CairoSVG.  If not, see <http://www.gnu.org/licenses/>.

"""
Cairo surface creator.

"""

# Ignore small variable names here
# pylint: disable=C0103

import abc
import cairo
import io
from math import pi, cos, sin, atan, radians

from .parser import Tree
from .colors import COLORS

DPI = 72.
UNITS = {
    "mm": 1 / 25.4,
    "cm": 1 / 2.54,
    "in": 1,
    "pt": 1 / 72.,
    "pc": 1 / 6.,
    "px": None,
    "em": NotImplemented,
    "ex": NotImplemented,
    "%": NotImplemented}
PATH_LETTERS = "achlmqstvzACHLMQSTVZ"
PATH_TAGS = ("circle", "line", "path", "polyline", "polygon", "rect")


def normalize(string=None):
    """Normalize a string corresponding to an array of various vaues."""
    string = string.replace("-", " -")
    string = string.replace(",", " ")

    while "  " in string:
        string = string.replace("  ", " ")

    return string


def size(string=None):
    """Replace a string with units by a float value."""
    if not string:
        return 0

    if string.replace(".", "", 1).lstrip(" -").isdigit():
        return float(string)

    for unit, coefficient in UNITS.items():
        if unit in string:
            number = float(string.strip(" " + unit))
            return number * (DPI * coefficient if coefficient else 1)


def color(string=None, opacity=1):
    """Replace ``string`` representing a color by a RGBA tuple."""
    if not string or string == "none":
        return (0, 0, 0, 0)

    string = string.strip().lower()

    if string.startswith("rgba"):
        r, g, b, a = tuple(float(i) for i in string.strip(" rgba()").split(","))
        return r, g, b, a * opacity
    elif string.startswith("rgb"):
        r, g, b = tuple(float(i) for i in string.strip(" rgb()").split(","))
        return r, g, b, opacity

    if string in COLORS:
        string = COLORS[string]

    if len(string) in (4, 5):
        string = "#" + "".join(2 * char for char in string[1:])
    if len(string) == 9:
        opacity *= int(string[7:9], 16)/255
    plain_color = tuple(int(value, 16)/255. for value in (
            string[1:3], string[3:5], string[5:7]))
    return plain_color + (opacity,)


def point(string=None):
    """Return ``(x, y, trailing_text)`` from ``string``."""
    if not string:
        return (0, 0, "")

    x, y, string = (string.strip() + " ").split(" ", 2)
    return size(x), size(y), string


def node_format(node):
    """Return ``(width, height, viewbox)`` of ``node``."""
    width = size(node.get("width"))
    height = size(node.get("height"))
    viewbox = node.get("viewBox")
    if viewbox:
        viewbox = tuple(size(pos) for pos in viewbox.split())
        width = width or viewbox[2]
        height = height or viewbox[3]
    return width, height, viewbox


def quadratic_points(x1, y1, x2, y2, x3, y3):
    """Return the quadratic points to create quadratic curves."""
    xq1 = x2 * 2 / 3 + x1 / 3
    yq1 = y2 * 2 / 3 + y1 / 3
    xq2 = x2 * 2 / 3 + x3 / 3
    yq2 = y2 * 2 / 3 + y3 / 3
    return xq1, yq1, xq2, yq2, x3, y3


def point_angle(cx, cy, px, py):
    """Return angle between x axis and point knowing given center."""
    angle = pi if cx > px else 0
    angle *= -1 if cy > py else 1
    angle += atan((cy - py) * (1 / (cx - px)) if (cx - px) else float("inf"))
    return angle


def rotate(x, y, angle):
    """Rotate a point of an angle around the origin point."""
    return x * cos(angle) - y * sin(angle), y * cos(angle) + x * sin(angle)


class Surface(object):
    """Cairo abstract surface."""
    # Cairo developers say that there is no way to inherit from cairo.*Surface
    __metaclass__ = abc.ABCMeta

    def __init__(self, tree):
        """Create the surface from ``tree``."""
        self.cairo = None
        self.context = None
        self.cursor_position = 0, 0
        self.bytesio = io.BytesIO()
        self._create_surface(tree)
        self.draw(tree)

    @abc.abstractmethod
    def _create_surface(self, tree):
        """Create a cairo surface.

        A method overriding this one must create ``self.cairo`` and
        ``self.context``.

        """
        raise NotImplementedError

    def _set_context_size(self, width, height, viewbox):
        """Set the context size."""
        if viewbox:
            x, y, x_size, y_size = viewbox
            x_ratio, y_ratio = width / x_size, height / y_size
            if x_ratio > y_ratio:
                self.context.translate((width - x_size * y_ratio) / 2, 0)
                self.context.scale(y_ratio, y_ratio)
                self.context.translate(-x, -y / y_ratio * x_ratio)
            elif x_ratio < y_ratio:
                self.context.translate(0, (height - y_size * x_ratio) / 2)
                self.context.scale(x_ratio, x_ratio)
                self.context.translate(-x / x_ratio * y_ratio, -y)
            else:
                self.context.scale(x_ratio, y_ratio)
                self.context.translate(-x, -y)

    def read(self):
        """Read the surface content."""
        self.cairo.finish()
        value = self.bytesio.getvalue()
        self.bytesio.close()
        return value

    def draw(self, node):
        """Draw ``node`` and its children."""
        # Ignore defs
        if node.tag == "defs":
            return

        self.context.save()
        self.context.move_to(size(node.get("x")), size(node.get("y")))

        # Transform the context according to the ``transform`` attribute
        if node.get("transform"):
            # TODO: check if multiple-depth transformations work correctly
            transformations = node["transform"].split(")")
            for transformation in transformations:
                for ttype in ("scale", "translate", "matrix", "rotate"):
                    if ttype in transformation:
                        transformation = transformation.replace(ttype, "")
                        transformation = transformation.replace("(", "")
                        transformation = normalize(transformation).strip() + " "
                        values = []
                        while transformation:
                            value, transformation = transformation.split(" ", 1)
                            values.append(size(value))
                        if ttype == "matrix":
                            matrix = cairo.Matrix(*values)
                            self.context.set_matrix(matrix)
                        elif ttype == "rotate":
                            self.context.rotate(radians(float(values[0])))
                        else:
                            if len(values) == 1:
                                values = 2 * values
                            getattr(self.context, ttype)(*values)

        if node.tag in PATH_TAGS:
            # Set 1 as default stroke-width
            if not node.get("stroke-width"):
                node["stroke-width"] = "1"

        # Set drawing informations of the node if the ``node.tag`` method exists
        if hasattr(self, node.tag):
            getattr(self, node.tag)(node)

        # Get stroke and fill opacity
        opacity = float(node.get("opacity", 1))
        stroke_opacity = opacity * float(node.get("stroke-opacity", 1))
        fill_opacity = opacity * float(node.get("fill-opacity", 1))

        # Fill
        if node.get("fill-rule") == "evenodd":
            self.context.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        self.context.set_source_rgba(*color(node.get("fill"), fill_opacity))
        self.context.fill_preserve()

        # Stroke
        self.context.set_line_width(size(node.get("stroke-width")))
        self.context.set_source_rgba(*color(node.get("stroke"), stroke_opacity))
        self.context.stroke()

        # Draw children
        for child in node.children:
            self.draw(child)

        if not node.root:
            # Restoring context is useless if we are in the root tag, it may
            # raise an exception if we have multiple svg tags
            self.context.restore()

    def circle(self, node):
        """Draw a circle ``node``."""
        self.context.new_sub_path()
        self.context.arc(
            size(node.get("x")) + size(node.get("cx")),
            size(node.get("y")) + size(node.get("cy")),
            size(node.get("r")), 0, 2 * pi)

    def ellipse(self, node):
        """Draw an ellipse ``node``."""
        y_scale_ratio = size(node.get("ry")) / size(node.get("rx"))
        self.context.new_sub_path()
        self.context.save()
        self.context.scale(1, y_scale_ratio)
        self.context.arc(
            size(node.get("x")) + size(node.get("cx")),
            (size(node.get("y")) + size(node.get("cy"))) / y_scale_ratio,
            size(node.get("rx")), 0, 2 * pi)
        self.context.restore()

    def path(self, node):
        """Draw a path ``node``."""
        string = node.get("d", "")

        for letter in PATH_LETTERS:
            string = string.replace(letter, " %s " % letter)

        last_letter = None

        string = normalize(string)
            
        while string:
            string = string.strip()
            if string.split(" ", 1)[0] in PATH_LETTERS:
                letter, string = (string + " ").split(" ", 1)
            if letter in "aA":
                # Elliptic curve
                x1, y1 = self.context.get_current_point()
                rx, ry, string = point(string)
                radii_ratio = ry / rx
                rotation, large, sweep, string = string.split(" ", 3)
                rotation = radians(float(rotation))
                large, sweep = bool(int(large)), bool(int(sweep))
                x3, y3, string = point(string)

                if letter == "A":
                    # Absolute x3 and y3, convert to relative
                    x3 -= x1
                    y3 -= y1

                # Cancel the rotation of the second point
                xe, ye = rotate(x3, y3, -rotation)
                ye /= radii_ratio

                # Find the angle between the second point and the x axis
                angle = point_angle(0, 0, xe, ye)

                # Put the second point onto the x axis
                xe = (xe**2 + ye**2)**.5
                ye = 0

                # Update the x radius if it is too small
                rx = max(rx, xe / 2)

                # Find one circle centre
                xc = xe / 2
                yc = (rx**2 - xc**2)**.5

                # Choose between the two circles according to flags
                if not (large ^ sweep):
                    yc = -yc

                # Define the arc sweep
                arc = self.context.arc if sweep else self.context.arc_negative

                # Put the second point and the center back to their positions
                xe, ye = rotate(xe, 0, angle)
                xc, yc = rotate(xc, yc, angle)

                # Find the drawing angles
                angle1 = point_angle(xc, yc, 0, 0)
                angle2 = point_angle(xc, yc, xe, ye)

                # Draw the arc
                self.context.save()
                self.context.translate(x1, y1)
                self.context.rotate(rotation)
                self.context.scale(1, radii_ratio)
                arc(xc, yc, rx, angle1, angle2)
                self.context.restore()
            elif letter == "c":
                # Relative curve
                x1, y1, string = point(string)
                x2, y2, string = point(string)
                x3, y3, string = point(string)
                self.context.rel_curve_to(x1, y1, x2, y2, x3, y3)
            elif letter == "C":
                # Curve
                x1, y1, string = point(string)
                x2, y2, string = point(string)
                x3, y3, string = point(string)
                self.context.curve_to(x1, y1, x2, y2, x3, y3)
            elif letter == "h":
                # Relative horizontal line
                x, string = string.split(" ", 1)
                self.context.rel_line_to(size(x), 0)
            elif letter == "H":
                # Horizontal line
                x, string = string.split(" ", 1)
                self.context.line_to(
                    size(x), self.context.get_current_point()[1])
            elif letter == "l":
                # Relative straight line
                x, y, string = point(string)
                self.context.rel_line_to(x, y)
            elif letter == "L":
                # Straight line
                x, y, string = point(string)
                self.context.line_to(x, y)
            elif letter == "m":
                # Current point relative move
                x, y, string = point(string)
                self.context.rel_move_to(x, y)
            elif letter == "M":
                # Current point move
                x, y, string = point(string)
                self.context.move_to(x, y)
            elif letter == "q":
                # Relative quadratic curve
                # TODO: manage next letter "T"
                string, next_string = string.split("t", 1)
                x1, y1 = 0, 0
                while string:
                    x2, y2, string = point(string)
                    x3, y3, string = point(string)
                    xq1, yq1, xq2, yq2, xq3, yq3 = quadratic_points(
                        x1, y1, x2, y2, x3, y3)
                    self.context.rel_curve_to(xq1, yq1, xq2, yq2, xq3, yq3)
                string = "t" + next_string
            elif letter == "Q":
                # Quadratic curve
                # TODO: manage next letter "t"
                string, next_string = string.split("T", 1)
                x1, y1 = self.context.get_current_point()
                while string:
                    x2, y2, string = point(string)
                    x3, y3, string = point(string)
                    xq1, yq1, xq2, yq2, xq3, yq3 = quadratic_points(
                        x1, y1, x2, y2, x3, y3)
                    self.context.curve_to(xq1, yq1, xq2, yq2, xq3, yq3)
                string = "T" + next_string
            elif letter == "s":
                # Relative smooth curve
                # TODO: manage last_letter in "CS"
                x1 = x3 - x2 if last_letter in "cs" else 0
                y1 = y3 - y2 if last_letter in "cs" else 0
                x2, y2, string = point(string)
                x3, y3, string = point(string)
                self.context.rel_curve_to(x1, y1, x2, y2, x3, y3)
            elif letter == "S":
                # Smooth curve
                # TODO: manage last_letter in "cs"
                x, y = self.context.get_current_point()
                x1 = x3 - x2 if last_letter in "CS" else x
                y1 = y3 - y2 if last_letter in "CS" else y
                x2, y2, string = point(string)
                x3, y3, string = point(string)
                self.context.curve_to(x1, y1, x2, y2, x3, y3)
            elif letter == "t":
                # Relative quadratic curve end
                x1, y1 = 0, 0
                x2 = 2 * x1 - x2
                y2 = 2 * y1 - y2
                x3, y3, string = point(string)
                xq1, yq1, xq2, yq2, xq3, yq3 = quadratic_points(
                    x1, y1, x2, y2, x3, y3)
                self.context.rel_curve_to(xq1, yq1, xq2, yq2, xq3, yq3)
            elif letter == "T":
                # Quadratic curve end
                x1, y1 = self.context.get_current_point()
                x2 = 2 * x1 - x2
                y2 = 2 * y1 - y2
                x3, y3, string = point(string)
                xq1, yq1, xq2, yq2, xq3, yq3 = quadratic_points(
                    x1, y1, x2, y2, x3, y3)
                self.context.curve_to(xq1, yq1, xq2, yq2, xq3, yq3)
            elif letter == "v":
                # Relative vertical line
                y, string = string.split(" ", 1)
                self.context.rel_line_to(0, size(y))
            elif letter == "V":
                # Vertical line
                y, string = string.split(" ", 1)
                self.context.line_to(
                    self.context.get_current_point()[0], size(y))
            elif letter in "zZ":
                # End of path
                self.context.close_path()
            else:
                # TODO: manage other letters
                raise NotImplementedError

            last_letter = letter

            string = string.strip()

    def line(self, node):
        """Draw a line ``node``."""
        x1, y1, x2, y2 = tuple(size(position) for position in (
                node.get("x1"), node.get("y1"), node.get("x2"), node.get("y2")))
        self.context.move_to(x1, y1)
        self.context.line_to(x2, y2)

    def polyline(self, node):
        """Draw a polyline ``node``."""
        points = normalize(node.get("points"))
        if points:
            x, y, points = point(points)
            self.context.move_to(x, y)
            while points:
                x, y, points = point(points)
                self.context.line_to(x, y)

    def polygon(self, node):
        """Draw a polygon ``node``."""
        self.polyline(node)
        self.context.close_path()

    def rect(self, node):
        """Draw a rect ``node``."""
        x, y = size(node.get("x")), size(node.get("y"))
        width, height = size(node.get("width")), size(node.get("height"))
        self.context.rectangle(x, y, width, height)

    def tref(self, node):
        """Draw a tref ``node``."""
        self.use(node)

    def tspan(self, node):
        """Draw a tspan ``node``."""
        x, y = self.cursor_position
        if "x" in node:
            x = size(node["x"])
        if "y" in node:
            y = size(node["y"])
        node["x"] = str(x + size(node.get("dx")))
        node["y"] = str(y + size(node.get("dy")))
        self.text(node)

    def text(self, node):
        """Draw a text ``node``."""
        # Set black as default text color
        if not node.get("fill"):
            node["fill"] = node.get("color") or "#000000"

        # TODO: find a better way to manage empty text nodes
        node.text = node.text.strip() if node.text else ""

        # TODO: manage font variant
        font_size = size(node.get("font-size", "12pt"))
        font_family = node.get("font-family", "Sans")
        font_style = getattr(
            cairo, ("font_slant_%s" % node.get("font-style")).upper(),
            cairo.FONT_SLANT_NORMAL)
        font_weight = getattr(
            cairo, ("font_weight_%s" % node.get("font-weight")).upper(),
            cairo.FONT_WEIGHT_NORMAL)
        self.context.select_font_face(font_family, font_style, font_weight)
        self.context.set_font_size(font_size)

        # TODO: manage y_bearing and *_advance
        x_bearing, y_bearing, width, height, x_advance, y_advance = \
            self.context.text_extents(node.text)
        x, y = size(node.get("x")), size(node.get("y"))
        text_anchor = node.get("text-anchor")
        if text_anchor == "middle":
            x -= width/2. + x_bearing
        elif text_anchor == "end":
            x -= width + x_bearing
        
        # Get global text opacity
        opacity = float(node.get("opacity", 1))

        self.context.move_to(x, y)
        self.context.set_source_rgba(*color(node.get("fill"), opacity))
        self.context.show_text(node.text)
        self.context.move_to(x, y)
        self.context.text_path(node.text)
        node["fill"] = "#00000000"

        # Remember the cursor position
        self.cursor_position = self.context.get_current_point()

    def use(self, node):
        """Draw the content of another SVG file."""
        self.context.save()
        self.context.translate(size(node.get("x")), size(node.get("y")))
        if "x" in node:
            del node["x"]
        if "y" in node:
            del node["y"]
        if "viewBox" in node:
            del node["viewBox"]
        href = node.get("{http://www.w3.org/1999/xlink}href")
        tree = Tree(href, node)
        self._set_context_size(*node_format(tree))
        self.draw(tree)
        self.context.restore()
        # Restore twice, because draw does not restore at the end of svg tags
        self.context.restore()

# pylint: enable=C0103
