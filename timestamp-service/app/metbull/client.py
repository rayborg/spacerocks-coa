from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Protocol, cast

import httpx

METBULL_HOST = "www.lpi.usra.edu"
METBULL_PATH = "/meteor/metbull.cfm"
METBULL_RESPONSE_LIMIT = 512 * 1024
METBULL_OFFICIAL_URL = f"https://{METBULL_HOST}{METBULL_PATH}?code={{code}}"
MAX_CONCURRENT_METBULL_REQUESTS = 4
_MAX_RESOLVED_ADDRESSES = 16
_MAX_HTML_NODES = 30_000
_MAX_HTML_DEPTH = 128
_CANONICAL_CODE = re.compile(r"^[1-9][0-9]{0,8}$")
_DECIMAL_COORDINATE = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_SPACE = re.compile(r"\s+")
_VOID_ELEMENTS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
)


class MetbullUnavailable(RuntimeError):
    pass


class MetbullNotFound(RuntimeError):
    pass


class MetbullNotOfficial(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MetbullRecord:
    code: int
    official_url: str
    canonical_name: str
    record_status: str
    official_name: bool
    recommended_classification: str
    fall_or_find: str
    year_found: int | None
    country: str | None
    latitude: str | None
    longitude: str | None


class MetbullLookup(Protocol):
    async def lookup(self, code: int) -> MetbullRecord: ...


AddressResolver = Callable[[str, int], Awaitable[Sequence[str]]]


class MeteoriticalBulletinClient:
    """Fetch only the fixed Meteoritical Bulletin record endpoint and parse a strict subset."""

    def __init__(
        self,
        timeout_seconds: float = 8.0,
        *,
        resolver: AddressResolver | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int | float)
            or not 0.1 <= timeout_seconds <= 30.0
        ):
            raise ValueError("metbull_timeout_out_of_bounds")
        self._timeout_seconds = float(timeout_seconds)
        self._timeout = httpx.Timeout(self._timeout_seconds)
        self._resolver = resolver or _resolve_addresses
        self._transport = transport
        self._request_slots = asyncio.Semaphore(MAX_CONCURRENT_METBULL_REQUESTS)

    async def lookup(self, code: int) -> MetbullRecord:
        canonical_code = _validated_code(code)
        try:
            async with asyncio.timeout(self._timeout_seconds):
                async with self._request_slots:
                    body = await self._fetch(canonical_code)
        except MetbullNotFound:
            raise
        except MetbullUnavailable:
            raise
        except asyncio.CancelledError:
            raise
        except (TimeoutError, httpx.HTTPError, OSError) as exc:
            raise MetbullUnavailable("metbull_upstream_unavailable") from exc
        return parse_metbull_record(body, code)

    async def _fetch(self, code: str) -> bytes:
        addresses = await self._resolver(METBULL_HOST, 443)
        vetted = _vetted_public_addresses(addresses)
        pinned = next((value for value in vetted if ipaddress.ip_address(value).version == 4), vetted[0])
        address = ipaddress.ip_address(pinned)
        authority = f"[{address.compressed}]" if address.version == 6 else address.compressed
        request = httpx.Request(
            "GET",
            f"https://{authority}{METBULL_PATH}?code={code}",
            headers={
                "Host": METBULL_HOST,
                "Accept": "text/html",
                "Accept-Encoding": "identity",
                "User-Agent": "spacerocks-coa-metbull/1",
            },
            extensions={"sni_hostname": METBULL_HOST},
        )
        body = bytearray()
        async with httpx.AsyncClient(
            follow_redirects=False,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
            timeout=self._timeout,
            trust_env=False,
            transport=self._transport,
        ) as client:
            response = await client.send(request, stream=True)
            try:
                if response.status_code == 404:
                    raise MetbullNotFound("metbull_record_not_found")
                if response.status_code != 200:
                    raise MetbullUnavailable("metbull_http_response_invalid")
                media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if media_type not in {"text/html", "application/xhtml+xml"}:
                    raise MetbullUnavailable("metbull_content_type_invalid")
                encoding = response.headers.get("content-encoding")
                if encoding is not None and encoding.strip().lower() != "identity":
                    raise MetbullUnavailable("metbull_content_encoding_invalid")
                declared_length = response.headers.get("content-length")
                if declared_length is not None:
                    try:
                        content_length = int(declared_length)
                    except ValueError as exc:
                        raise MetbullUnavailable("metbull_content_length_invalid") from exc
                    if not 1 <= content_length <= METBULL_RESPONSE_LIMIT:
                        raise MetbullUnavailable("metbull_content_length_invalid")
                async for chunk in response.aiter_raw():
                    if len(chunk) > METBULL_RESPONSE_LIMIT - len(body):
                        raise MetbullUnavailable("metbull_response_too_large")
                    body.extend(chunk)
            finally:
                await response.aclose()
        if not body:
            raise MetbullUnavailable("metbull_response_empty")
        return bytes(body)


@dataclass(slots=True)
class _Node:
    tag: str
    attrs: dict[str, str]
    children: list[_Node | str]


class _BoundedTreeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("document", {}, [])
        self._stack = [self.root]
        self._nodes = 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._append_node(tag, attrs, tag not in _VOID_ELEMENTS)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._append_node(tag, attrs, False)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if data:
            self._stack[-1].children.append(data)

    def _append_node(self, tag: str, attrs: list[tuple[str, str | None]], push: bool) -> None:
        self._nodes += 1
        if self._nodes > _MAX_HTML_NODES:
            raise MetbullUnavailable("metbull_html_too_complex")
        if len(attrs) != len({name.casefold() for name, _value in attrs}):
            raise MetbullUnavailable("metbull_html_attribute_ambiguous")
        node = _Node(tag.casefold(), {name.casefold(): value or "" for name, value in attrs}, [])
        self._stack[-1].children.append(node)
        if push:
            if len(self._stack) >= _MAX_HTML_DEPTH:
                raise MetbullUnavailable("metbull_html_too_deep")
            self._stack.append(node)


def parse_metbull_record(raw_html: bytes, expected_code: int) -> MetbullRecord:
    _validated_code(expected_code)
    if not raw_html or len(raw_html) > METBULL_RESPONSE_LIMIT:
        raise MetbullUnavailable("metbull_response_size_invalid")
    try:
        html = raw_html.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MetbullUnavailable("metbull_html_encoding_invalid") from exc
    parser = _BoundedTreeParser()
    try:
        parser.feed(html)
        parser.close()
    except MetbullUnavailable:
        raise
    except Exception as exc:
        raise MetbullUnavailable("metbull_html_invalid") from exc

    mains = [node for node in _descendants(parser.root, "main") if "container" in _classes(node)]
    if len(mains) != 1:
        raise MetbullUnavailable("metbull_entry_scope_invalid")
    main = mains[0]
    if _is_official_unknown_entry(parser.root, main, expected_code):
        raise MetbullNotFound("metbull_record_not_found")

    names = [_bounded_text(_text(node), 128) for node in _descendants(main, "h1")]
    if len(names) != 1:
        raise MetbullUnavailable("metbull_name_ambiguous")
    canonical_name = names[0]

    direct_path = f"{METBULL_PATH}?code={expected_code}"
    direct_links = [
        node for node in _descendants(main, "a") if node.attrs.get("href") == direct_path
    ]
    if len(direct_links) != 1:
        raise MetbullUnavailable("metbull_page_code_invalid")

    sections = _entry_sections(main)
    basic = _require_section(sections, "Basic information")

    statuses = []
    for alert in (node for node in _descendants(main, "div") if "alert" in _classes(node)):
        labels = _labeled_values(alert)
        if "Status" in labels:
            statuses.extend(labels["Status"])
    record_status = _single_bounded(statuses, "metbull_status_ambiguous", 64)
    if "unofficial" in record_status.casefold():
        raise MetbullNotOfficial("metbull_name_not_official")

    basic_values = _labeled_values(basic)
    name_statement = _single_bounded(basic_values.get("Name", []), "metbull_official_name_invalid", 512)
    if not name_statement.startswith(canonical_name):
        raise MetbullUnavailable("metbull_name_incoherent")
    statement = name_statement[len(canonical_name) :].strip()
    if re.search(r"\b(?:unofficial|not an official) name\b", statement, re.IGNORECASE):
        raise MetbullNotOfficial("metbull_name_not_official")
    if re.search(r"\bThis is an official name(?:\.|\s+for\b)", statement) is None:
        raise MetbullUnavailable("metbull_official_statement_missing")
    if record_status == "Relict":
        if not statement.startswith("This is an official name for a relict meteorite."):
            raise MetbullUnavailable("metbull_status_statement_incoherent")
    elif record_status == "Official":
        if not statement.startswith("This is an official name."):
            raise MetbullUnavailable("metbull_status_statement_incoherent")
    elif record_status.casefold() in {"discredited", "doubtful", "provisional", "unofficial"}:
        raise MetbullNotOfficial("metbull_name_not_official")
    else:
        raise MetbullUnavailable("metbull_status_unknown")

    classification = _require_section(sections, "Classification history")
    geography = _require_section(sections, "Geography")
    recommended = _single_bounded(
        _labeled_values(classification).get("Recommended", []),
        "metbull_classification_ambiguous",
        128,
    )
    if record_status.casefold() == "relict" and not recommended.casefold().startswith("relict "):
        raise MetbullUnavailable("metbull_status_classification_incoherent")
    observed = _single_bounded(basic_values.get("Observed fall", []), "metbull_fall_status_ambiguous", 8)
    if observed == "Yes":
        fall_or_find = "Fall"
    elif observed == "No":
        fall_or_find = "Find"
    else:
        raise MetbullUnavailable("metbull_fall_status_invalid")

    year_found = _optional_year(basic_values.get("Year found", []))
    basic_country = _optional_single(basic_values.get("Country", []), "metbull_country_ambiguous", 128)
    geography_values = _labeled_values(geography)
    geography_country = _optional_single(
        geography_values.get("Country", []), "metbull_country_ambiguous", 128
    )
    if basic_country is not None and geography_country is not None and basic_country != geography_country:
        raise MetbullUnavailable("metbull_country_incoherent")
    country = basic_country or geography_country
    latitude, longitude = _coordinates(geography_values.get("Coordinates", []))

    return MetbullRecord(
        code=expected_code,
        official_url=METBULL_OFFICIAL_URL.format(code=expected_code),
        canonical_name=canonical_name,
        record_status=record_status,
        official_name=True,
        recommended_classification=recommended,
        fall_or_find=fall_or_find,
        year_found=year_found,
        country=country,
        latitude=latitude,
        longitude=longitude,
    )


def _entry_sections(main: _Node) -> dict[str, list[_Node]]:
    sections: dict[str, list[_Node]] = {}
    for row in (node for node in _descendants(main, "tr") if "row" in _classes(node)):
        headings = [_normalized_label(_heading_text(node)) for node in _child_elements(row, "th")]
        values = _descendants(row, "td")
        if len(headings) == 1 and len(values) == 1:
            sections.setdefault(headings[0], []).append(values[0])
    return sections


def _require_section(sections: dict[str, list[_Node]], name: str) -> _Node:
    values = sections.get(name, [])
    if len(values) != 1:
        raise MetbullUnavailable("metbull_entry_section_invalid")
    return values[0]


def _labeled_values(node: _Node) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    for parent in _walk_nodes(node):
        for index, child in enumerate(parent.children):
            if not isinstance(child, _Node) or child.tag != "strong":
                continue
            label = _normalized_label(_text(child))
            parts: list[str] = []
            for sibling in parent.children[index + 1 :]:
                if isinstance(sibling, _Node) and sibling.tag in {"a", "br", "button", "strong"}:
                    break
                parts.append(_text(sibling) if isinstance(sibling, _Node) else sibling)
            values.setdefault(label, []).append(_normalize("".join(parts)))
    return values


def _coordinates(values: list[str]) -> tuple[str | None, str | None]:
    raw = _single_bounded(values, "metbull_coordinates_ambiguous", 128)
    if raw.rstrip(".").casefold() == "unknown":
        return None, None
    parts = [part.strip() for part in raw.split(",")]
    if len(parts) != 2:
        raise MetbullUnavailable("metbull_coordinates_invalid")
    latitude = _validated_coordinate(parts[0], Decimal("-90"), Decimal("90"))
    longitude = _validated_coordinate(parts[1], Decimal("-180"), Decimal("180"))
    return latitude, longitude


def _validated_coordinate(raw: str, minimum: Decimal, maximum: Decimal) -> str:
    if not _DECIMAL_COORDINATE.fullmatch(raw):
        raise MetbullUnavailable("metbull_coordinates_invalid")
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise MetbullUnavailable("metbull_coordinates_invalid") from exc
    if not minimum <= value <= maximum:
        raise MetbullUnavailable("metbull_coordinates_invalid")
    return raw


def _optional_year(values: list[str]) -> int | None:
    if not values:
        return None
    raw = _single_bounded(values, "metbull_year_ambiguous", 4)
    if re.fullmatch(r"[0-9]{4}", raw) is None:
        raise MetbullUnavailable("metbull_year_invalid")
    year = int(raw)
    if not 1 <= year <= 9999:
        raise MetbullUnavailable("metbull_year_invalid")
    return year


def _optional_single(values: list[str], error: str, maximum: int) -> str | None:
    if not values:
        return None
    return _single_bounded(values, error, maximum)


def _single_bounded(values: list[str], error: str, maximum: int) -> str:
    if len(values) != 1:
        raise MetbullUnavailable(error)
    return _bounded_text(values[0], maximum)


def _bounded_text(value: str, maximum: int) -> str:
    normalized = _normalize(value)
    if not 1 <= len(normalized) <= maximum or any(ord(character) < 0x20 for character in normalized):
        raise MetbullUnavailable("metbull_field_invalid")
    return normalized


def _normalized_label(value: str) -> str:
    return _normalize(value).rstrip(":").strip()


def _heading_text(node: _Node) -> str:
    return "".join(
        child if isinstance(child, str) else _text(child)
        for child in node.children
        if isinstance(child, str) or child.tag != "td"
    )


def _normalize(value: str) -> str:
    return _SPACE.sub(" ", value).strip()


def _is_official_unknown_entry(root: _Node, main: _Node, expected_code: int) -> bool:
    titles = [_bounded_text(_text(node), 64) for node in _descendants(root, "title")]
    if titles != ["Meteoritical Bulletin: Entry for unknown meteorite"]:
        return False
    robots = [node for node in _descendants(root, "meta") if node.attrs.get("name", "").casefold() == "robots"]
    if (
        len(robots) != 1
        or robots[0].attrs != {"name": "robots", "content": "noindex,nofollow"}
        or main.attrs != {"class": "container", "role": "main"}
    ):
        return False
    rows = _child_elements(main, "div")
    if len(rows) != 1 or rows[0].attrs != {"class": "row"}:
        return False
    columns = _child_elements(rows[0], "div")
    if len(columns) != 1 or columns[0].attrs != {"class": "col-12"}:
        return False
    column_elements = [child for child in columns[0].children if isinstance(child, _Node)]
    if [node.tag for node in column_elements] != ["div", "style"]:
        return False
    link_wrappers = _child_elements(column_elements[0], "a")
    if len(link_wrappers) != 1:
        return False
    direct_path = f"{METBULL_PATH}?code={expected_code}"
    direct_links = [
        node
        for node in link_wrappers
        if node.attrs.get("href") == direct_path
        and _classes(node) == {"button-darkblue", "mb-5"}
        and _normalize(_text(node)) == "Direct link to this page"
    ]
    real_entry_tags = {"h1", "table", "tr"}
    has_real_entry = any(node.tag in real_entry_tags for node in _walk_nodes(main)[1:]) or any(
        "alert" in _classes(node) for node in _descendants(main, "div")
    )
    return len(direct_links) == 1 and not has_real_entry


def _classes(node: _Node) -> set[str]:
    return set(node.attrs.get("class", "").split())


def _text(value: _Node | str) -> str:
    if isinstance(value, str):
        return value
    return "".join(_text(child) for child in value.children)


def _walk_nodes(node: _Node) -> list[_Node]:
    pending = [node]
    result: list[_Node] = []
    while pending:
        current = pending.pop()
        result.append(current)
        pending.extend(child for child in reversed(current.children) if isinstance(child, _Node))
    return result


def _descendants(node: _Node, tag: str) -> list[_Node]:
    return [candidate for candidate in _walk_nodes(node)[1:] if candidate.tag == tag]


def _child_elements(node: _Node, tag: str) -> list[_Node]:
    return [child for child in node.children if isinstance(child, _Node) and child.tag == tag]


def _vetted_public_addresses(addresses: Sequence[str]) -> tuple[str, ...]:
    if not 1 <= len(addresses) <= _MAX_RESOLVED_ADDRESSES:
        raise MetbullUnavailable("metbull_dns_response_invalid")
    normalized: list[str] = []
    for raw_address in addresses:
        try:
            if not isinstance(raw_address, str) or "%" in raw_address:
                raise ValueError
            address = ipaddress.ip_address(raw_address)
        except ValueError as exc:
            raise MetbullUnavailable("metbull_dns_response_invalid") from exc
        if not _is_globally_routable_unicast(address):
            raise MetbullUnavailable("metbull_dns_address_not_public")
        if address.compressed not in normalized:
            normalized.append(address.compressed)
    if not normalized:
        raise MetbullUnavailable("metbull_dns_response_invalid")
    return tuple(normalized)


def _is_globally_routable_unicast(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        address.is_global
        and not address.is_multicast
        and not address.is_private
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_reserved
        and not address.is_unspecified
        and not (isinstance(address, ipaddress.IPv6Address) and address.is_site_local)
    )


async def _resolve_addresses(hostname: str, port: int) -> tuple[str, ...]:
    results = await asyncio.to_thread(
        socket.getaddrinfo,
        hostname,
        port,
        type=socket.SOCK_STREAM,
        proto=socket.IPPROTO_TCP,
    )
    return tuple(cast(str, result[4][0]) for result in results)


def _validated_code(code: object) -> str:
    if type(code) is not int:
        raise ValueError("metbull_code_invalid")
    canonical = str(code)
    if not _CANONICAL_CODE.fullmatch(canonical):
        raise ValueError("metbull_code_invalid")
    return canonical
