"""Loader de paginas web para o RAG -- busca uma URL publica e extrai o texto.

Como a URL vem de fora (do usuario ou de quem ingere conhecimento), o acesso e defensivo:

- so `http`/`https`, sem credenciais na URL, apenas portas padrao (80/443);
- **protecao contra SSRF**: o host e resolvido e todo IP precisa ser publico (`ipaddress.is_global`
  -- rejeita loopback, rede privada, link-local/metadata 169.254.x, CGNAT etc.); a conexao e feita
  no IP ja validado (com `Host`/SNI do dominio original), o que impede DNS rebinding;
- redirecionamentos seguidos manualmente (limite configuravel), cada salto revalidado;
- timeout, teto de bytes e apenas conteudo textual (HTML/texto);
- lista de dominios permitidos opcional (`WEB_ALLOWED_DOMAINS`);
- o texto extraido passa pelo scanner de entrada (injecao de prompt) e continua sendo tratado como
  dado nao confiavel pelos agentes (`wrap_untrusted_content`).
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import unicodedata
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
import structlog

from app.config.settings import get_settings
from app.security.scanners import get_input_scanner

logger = structlog.get_logger(__name__)

_ALLOWED_PORTS = {None, 80, 443}
_STEM_LENGTH = 6
_MIN_HEADER_CHARS = 200  # texto antes do <h1> so e descartado se passar disto...
_MAX_HEADER_FRACTION = 0.6  # ...e for no maximo esta fracao da pagina
_ALLOWED_CONTENT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
_URL_PATTERN = re.compile(r"https?://[^\s<>\"'`)\]]+", re.IGNORECASE)
_TRAILING_PUNCTUATION = ".,;:!?"


class WebFetchError(Exception):
    """Falha ao obter/validar uma pagina web. `code` e seguro para devolver ao cliente."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class WebPage:
    url: str
    title: str
    text: str
    # Links (url absoluta, texto ancora) do MESMO site -- base do seguimento de links.
    links: list[tuple[str, str]] = field(default_factory=list)
    truncated: bool = False  # a pagina passou de WEB_MAX_BYTES e foi cortada


def extract_urls(text: str, *, limit: int) -> list[str]:
    """URLs http(s) distintas presentes em `text` (na ordem em que aparecem)."""
    found: list[str] = []
    for match in _URL_PATTERN.findall(text):
        url = match.rstrip(_TRAILING_PUNCTUATION)
        if url and url not in found:
            found.append(url)
        if len(found) >= limit:
            break
    return found


def strip_urls(text: str) -> str:
    """`text` sem as URLs (para embedar so a pergunta)."""
    return re.sub(r"\s+", " ", _URL_PATTERN.sub(" ", text)).strip()


def validate_source_url(url: str) -> str:
    """Valida a FORMA de uma URL (esquema, credenciais, porta, dominio permitido) sem acessar a
    rede; devolve a URL sem espacos. O bloqueio de IPs internos acontece na hora do acesso."""
    _validate_url_shape(url)
    return url.strip()


# --- validacao de URL / SSRF ---------------------------------------------------------------------


def _allowed_domains() -> list[str]:
    raw = get_settings().web_allowed_domains
    return [d.strip().lower().lstrip(".") for d in raw.split(",") if d.strip()]


def _host_allowed(host: str) -> bool:
    allowed = _allowed_domains()
    if not allowed:
        return True
    return any(host == domain or host.endswith("." + domain) for domain in allowed)


def _validate_url_shape(url: str) -> tuple[str, str, int | None]:
    """Valida esquema/credenciais/porta/dominio; devolve (host, esquema, porta)."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError as exc:
        raise WebFetchError("WEB_URL_INVALID", "URL invalida.") from exc
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise WebFetchError("WEB_URL_INVALID", "Somente URLs http/https sao aceitas.")
    if parts.username or parts.password:
        raise WebFetchError("WEB_URL_NOT_ALLOWED", "URLs com credenciais nao sao aceitas.")
    if port not in _ALLOWED_PORTS:
        raise WebFetchError("WEB_URL_NOT_ALLOWED", "Porta nao permitida (use 80/443).")
    host = parts.hostname.lower().rstrip(".")
    if not _host_allowed(host):
        raise WebFetchError("WEB_URL_NOT_ALLOWED", "Dominio nao permitido para consulta web.")
    return host, parts.scheme, port


async def _resolve_public_ips(host: str) -> list[str]:
    """Resolve o host e exige que TODO endereco seja publico (senao, bloqueia)."""
    try:
        literal = ipaddress.ip_address(host)
        addresses = [str(literal)]
    except ValueError:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(
                host, None, type=socket.SOCK_STREAM
            )
        except OSError as exc:
            raise WebFetchError("WEB_FETCH_FAILED", "Nao foi possivel resolver o dominio.") from exc
        addresses = list(dict.fromkeys(info[4][0] for info in infos))
    if not addresses:
        raise WebFetchError("WEB_FETCH_FAILED", "Nao foi possivel resolver o dominio.")
    for address in addresses:
        if not ipaddress.ip_address(address.split("%")[0]).is_global:
            raise WebFetchError("WEB_URL_NOT_ALLOWED", "Endereco de rede nao permitido.")
    return addresses


def _pinned_url(url: str, ip: str) -> str:
    """Mesma URL, mas conectando ao IP ja validado (evita nova resolucao/DNS rebinding)."""
    parts = urlsplit(url)
    host = f"[{ip}]" if ":" in ip else ip
    netloc = host if parts.port is None else f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))


# --- extracao de texto ---------------------------------------------------------------------------

_SKIP_TAGS = {
    "script", "style", "noscript", "template", "svg", "iframe", "canvas", "nav", "footer", "form",
}  # fmt: skip
_BLOCK_TAGS = {
    "p", "div", "section", "article", "main", "br", "li", "ul", "ol", "tr", "table", "h1", "h2",
    "h3", "h4", "h5", "h6", "blockquote", "pre", "dt", "dd",
}  # fmt: skip


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self._skip_depth = 0
        self._parts: list[str] = []
        self._h1_mark: int | None = None  # posicao em _parts onde comeca o 1o <h1>
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._anchor_text: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "a":  # links de menu (nav/footer) contam: levam as paginas de conteudo
            self._href = dict(attrs).get("href")
            self._anchor_text = []
        if tag == "h1" and self._h1_mark is None and not self._skip_depth:
            self._h1_mark = len(self._parts)
        if tag == "title":
            self._in_title = True
        elif tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            text = re.sub(r"\s+", " ", "".join(self._anchor_text)).strip()
            self.links.append((self._href, text))
            self._href = None
        if tag == "title":
            self._in_title = False
        elif tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._anchor_text.append(data)
        if self._in_title:
            self.title += data
        elif not self._skip_depth:
            self._parts.append(data)

    def text(self) -> str:
        parts = self._parts
        # Paginas de conteudo comecam no <h1>; o que vem antes e cabecalho/menu do site (repetido em
        # todas as paginas). So descarta se for bastante texto e minoria da pagina (conservador).
        if self._h1_mark:
            before = len("".join(parts[: self._h1_mark]).strip())
            total = len("".join(parts).strip())
            if before >= _MIN_HEADER_CHARS and before <= total * _MAX_HEADER_FRACTION:
                parts = parts[self._h1_mark :]
        joined = "".join(parts)
        lines = (re.sub(r"[ \t\f\v]+", " ", line).strip() for line in joined.splitlines())
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def extract_page(content_type: str, body: str) -> tuple[str, str, list[tuple[str, str]]]:
    """(titulo, texto, links brutos) a partir do corpo HTML/texto."""
    if content_type.startswith("text/plain"):
        return "", body.strip(), []
    parser = _TextExtractor()
    parser.feed(body)
    parser.close()
    title = re.sub(r"\s+", " ", parser.title).strip()
    return title, parser.text(), parser.links


def extract_text(content_type: str, body: str) -> tuple[str, str]:
    """(titulo, texto) a partir do corpo HTML/texto."""
    title, text, _links = extract_page(content_type, body)
    return title, text


_MAX_LINKS = 300
_SKIPPED_LINK_PREFIXES = ("#", "javascript:", "mailto:", "tel:", "data:")


def same_site_links(base_url: str, raw_links: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Links absolutos http(s) do mesmo host da pagina (sem ancora, sem duplicatas)."""
    base_host = (urlsplit(base_url).hostname or "").lower()
    found: dict[str, str] = {}
    for href, text in raw_links:
        href = (href or "").strip()
        if not href or href.lower().startswith(_SKIPPED_LINK_PREFIXES):
            continue
        absolute = urljoin(base_url, href).split("#", 1)[0]
        parts = urlsplit(absolute)
        if parts.scheme not in ("http", "https") or (parts.hostname or "").lower() != base_host:
            continue
        if absolute.rstrip("/") == base_url.split("#", 1)[0].rstrip("/"):
            continue
        found.setdefault(absolute, text)
        if len(found) >= _MAX_LINKS:
            break
    return list(found.items())


_STOPWORDS = frozenset(
    "que para com uma dos das como qual quais sobre segundo the and for from with este esta "
    "isso mais mas por nos nas ser ter tem foi sao".split()
)


_ERROR_CODE = re.compile(r"(?<!\d)(\d)\s?-\s?(\d{2})(?!\d)|(?:adq|idl)\s*(\d)\s+(\d{2})(?!\d)")


_ERROR_CODE_TEXT = re.compile(
    r"(?<![a-z0-9])(?P<family>adq|idl)\s*(?P<a>\d)\s*-?\s*(?P<b>\d{2})(?!\d)"
    r"|(?<![a-z0-9])s\s?-\s?(?P<s>\d{4})(?!\d)"
)


def error_codes(text: str) -> set[str]:
    """Codigos de erro da maquininha no texto: "ADQ 4-91" -> "adq491", "S-2074" -> "s2074"."""
    folded = unicodedata.normalize("NFKD", text.lower())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    codes: set[str] = set()
    for match in _ERROR_CODE_TEXT.finditer(folded):
        if match.group("family"):
            codes.add(f"{match.group('family')}{match.group('a')}{match.group('b')}")
        else:
            codes.add(f"s{match.group('s')}")
    return codes


def tokens(text: str) -> set[str]:
    """Palavras normalizadas (minusculas, sem acento, >= 3 letras, sem stopwords)."""
    folded = unicodedata.normalize("NFKD", text.lower())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    words = {t for t in re.findall(r"[a-z0-9]{3,}", folded) if t not in _STOPWORDS}
    # Codigos de erro da maquininha ("ADQ 1-87", "IDL 1-04"): os numeros tem menos de 3 caracteres e
    # seriam descartados, entao "1-87" e "4-83" ficariam iguais. Viram um token unico ("c187").
    words |= {f"c{m[0] or m[2]}{m[1] or m[3]}" for m in _ERROR_CODE.findall(folded)}
    # plural simples ("taxas" ~ "taxa") e raiz de 6 letras ("habilito" ~ "habilitar", "estorno" ~
    # "estornar") para casar a pergunta com texto de link/fonte apesar da conjugacao.
    singular = {w[:-1] if len(w) > 4 and w.endswith("s") else w for w in words}
    return {w[:_STEM_LENGTH] for w in singular}


def select_relevant_links(page: WebPage, question: str, *, limit: int) -> list[tuple[str, str]]:
    """Links da pagina cujo texto/caminho tem palavras da pergunta (melhores primeiro).

    So segue um link se ha ao menos uma palavra em comum -- nunca navega "as cegas"."""
    wanted = tokens(question)
    if not wanted or limit <= 0:
        return []
    scored: list[tuple[int, int, str, str]] = []
    for link, text in page.links:
        score = len(wanted & tokens(f"{text} {urlsplit(link).path}"))
        if score:
            scored.append((-score, len(link), link, text))
    scored.sort()
    return [(link, text) for _s, _n, link, text in scored[:limit]]


# --- download ------------------------------------------------------------------------------------


def _build_client() -> httpx.AsyncClient:
    """Ponto unico de criacao do cliente HTTP (os testes o substituem por um MockTransport)."""
    settings = get_settings()
    return httpx.AsyncClient(
        timeout=settings.web_fetch_timeout_seconds,
        follow_redirects=False,
        headers={
            "User-Agent": "GetnetSupportBot/1.0 (+rag-web-loader)",
            "Accept": "text/html,text/plain",
        },
    )


async def _read_limited(response: httpx.Response, limit: int) -> tuple[bytes, bool]:
    """Le no maximo `limit` bytes (nunca a pagina inteira); `True` se foi cortada."""
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body.extend(chunk)
        if len(body) > limit:
            return bytes(body[:limit]), True
    return bytes(body), False


async def fetch_web_page(url: str) -> WebPage:
    """Busca `url` com as protecoes descritas no modulo e devolve titulo + texto limpo."""
    settings = get_settings()
    if not settings.web_access_enabled:
        raise WebFetchError("WEB_ACCESS_DISABLED", "O acesso a paginas web esta desativado.")

    current = url.strip()
    async with _build_client() as client:
        for _hop in range(settings.web_max_redirects + 1):
            host, _scheme, port = _validate_url_shape(current)
            host_header = host if port is None else f"{host}:{port}"
            ip = (await _resolve_public_ips(host))[0]
            try:
                async with client.stream(
                    "GET",
                    _pinned_url(current, ip),
                    headers={"Host": host_header},
                    extensions={"sni_hostname": host},
                ) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise WebFetchError("WEB_FETCH_FAILED", "Redirecionamento invalido.")
                        current = urljoin(current, location)
                        continue
                    if response.status_code != 200:
                        raise WebFetchError(
                            "WEB_FETCH_FAILED", f"A pagina respondeu HTTP {response.status_code}."
                        )
                    raw_type = response.headers.get("content-type", "")
                    content_type = raw_type.split(";")[0].strip().lower()
                    if content_type not in _ALLOWED_CONTENT_TYPES:
                        raise WebFetchError(
                            "WEB_CONTENT_TYPE_NOT_SUPPORTED", "Tipo de conteudo nao suportado."
                        )
                    raw, truncated = await _read_limited(response, settings.web_max_bytes)
                    charset = response.charset_encoding or "utf-8"
            except WebFetchError:
                raise
            except httpx.HTTPError as exc:
                logger.info("web_fetch_failed", error_type=type(exc).__name__)
                raise WebFetchError("WEB_FETCH_FAILED", "Falha ao acessar a pagina.") from exc

            title, text, raw_links = extract_page(
                content_type, raw.decode(charset, errors="replace")
            )
            text = text[: settings.web_max_chars].strip()
            if not text:
                raise WebFetchError("WEB_CONTENT_EMPTY", "A pagina nao tem texto aproveitavel.")
            scan = get_input_scanner().scan(f"{title}\n{text}")
            if not scan.is_safe:
                logger.warning("web_content_blocked", reason=scan.reason_code)
                raise WebFetchError(
                    "WEB_CONTENT_BLOCKED",
                    "O conteudo da pagina foi bloqueado pela politica de seguranca.",
                )
            return WebPage(
                url=url.strip(),
                title=title or urlsplit(url).hostname or url,
                text=text,
                links=same_site_links(current, raw_links),
                truncated=truncated,
            )
    raise WebFetchError("WEB_FETCH_FAILED", "Redirecionamentos em excesso.")
