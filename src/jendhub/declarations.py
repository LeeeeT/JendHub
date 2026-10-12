import re

DECLARATION = re.compile(r"^(?:@unsafe )?(?:def|type|law) ([A-Za-z_][\w.]*)", re.MULTILINE)
IMPORT_ALIAS = re.compile(r"^import \S+ as (\w+)", re.MULTILINE)


def declared_names(source: str) -> set[str]:
    aliases = tuple(f"{alias}." for alias in IMPORT_ALIAS.findall(source))
    return {name for name in DECLARATION.findall(source) if not name.startswith(aliases)}
