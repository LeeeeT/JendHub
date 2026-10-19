const FILTERS = ["package", "uses", "used_by"];
const QUERY = "query";

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
	let place = null;
	for (const word of text.trim().split(/\s+/).filter(Boolean)) {
		const found = filter(word);
		if (found) {
			pairs.push(found);
		} else {
			place ??= pairs.length;
			words.push(word);
		}
	}
	if (words.length) {
		pairs.splice(place, 0, [QUERY, words.join(" ")]);
	}
	return `/search?${pairs.map(([name, value]) => `${name}=${readable(value)}`).join("&")}`;
}

function boxText(params) {
	return [...params]
		.filter(([name]) => name === QUERY || FILTERS.includes(name))
		.map(([name, value]) => (name === QUERY ? value.trim() : `${name}=${value}`))
		.filter(Boolean)
		.join(" ");
}

const forms = document.querySelectorAll(".search-form");
for (const form of forms) {
	form.addEventListener("submit", (event) => {
		event.preventDefault();
		location.assign(searchUrl(form.elements.query.value));
	});
}

if (location.pathname === "/search") {
	const given = new URLSearchParams(location.search);
	const text = boxText(given);
	if ((given.get(QUERY) ?? "").split(/\s+/).some((word) => filter(word))) {
		location.replace(searchUrl(text));
	} else {
		for (const form of forms) {
			form.elements.query.value = text;
		}
	}
}
