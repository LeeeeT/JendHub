const FILTERS = ["package", "uses", "used_in"];

function readable(value) {
	return encodeURIComponent(value).replace(/%2F/g, "/").replace(/%3A/g, ":").replace(/%40/g, "@");
}

function filter(word) {
	const equals = word.indexOf("=");
	const name = word.slice(0, equals);
	return FILTERS.includes(name) && equals < word.length - 1 ? [name, word.slice(equals + 1)] : null;
}

function searchUrl(text) {
	const pairs = [];
	const words = [];
	for (const word of text.trim().split(/\s+/).filter(Boolean)) {
		const found = filter(word);
		if (found) {
			pairs.push(found);
		} else {
			words.push(word);
		}
	}
	if (words.length) {
		pairs.push(["query", words.join(" ")]);
	}
	return `/search?${pairs.map(([name, value]) => `${name}=${readable(value)}`).join("&")}`;
}

for (const form of document.querySelectorAll(".search-form")) {
	form.addEventListener("submit", (event) => {
		event.preventDefault();
		location.assign(searchUrl(form.elements.query.value));
	});
}

const given = new URLSearchParams(location.search);
const query = given.get("query") ?? "";
if (location.pathname === "/search" && query.split(/\s+/).some((word) => filter(word))) {
	const filters = FILTERS.flatMap((name) => given.getAll(name).map((value) => `${name}=${value}`));
	location.replace(searchUrl([...filters, query].join(" ")));
}
