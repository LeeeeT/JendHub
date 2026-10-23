'use strict';

const SPACE = /([\t-\r\x1c-\x20\x85\xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+)/;
const FOLLOWED = [
	'input',
	'scroll',
	'keydown',
	'keyup',
	'pointerdown',
	'pointermove',
	'pointerup',
	'focus',
	'blur',
	'select',
	'selectionchange',
];

function span(name, text) {
	const element = document.createElement('span');
	element.className = name;
	element.textContent = text;
	return element;
}

function marked(text, filters) {
	return text.split(SPACE).flatMap((word) => {
		const equals = word.indexOf('=');
		const name = word.slice(0, equals);
		const value = word.slice(equals + 1);
		return equals > 0 && value && filters.has(name)
			? [span('filter', `${name}=`), span('filter-value', value)]
			: [word];
	});
}

function highlight(form) {
	const input = form.querySelector('input[name="query"]');
	const filters = new Set(form.dataset.filters.split(' '));
	const marks = span('search-marks', '');
	const line = span('search-line', '');
	marks.setAttribute('aria-hidden', 'true');
	marks.append(line);
	input.before(marks);
	let drawn = null;
	let pending = false;
	const draw = () => {
		pending = false;
		if (input.value !== drawn) {
			drawn = input.value;
			line.replaceChildren(...marked(drawn, filters));
		}
		line.style.transform = `translateX(${-input.scrollLeft}px)`;
	};
	const follow = () => {
		if (!pending) {
			pending = true;
			requestAnimationFrame(draw);
		}
	};
	for (const name of FOLLOWED) {
		input.addEventListener(name, follow);
	}
	document.addEventListener('selectionchange', follow);
	window.addEventListener('resize', follow);
	window.addEventListener('pageshow', follow);
	draw();
	form.classList.add('highlighted');
}

document.querySelectorAll('form.search-form[data-filters]').forEach(highlight);
