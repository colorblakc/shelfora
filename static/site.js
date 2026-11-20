// Home shelf (category filters and scroll buttons), confirm prompts on forms, seat selection, double-submit protection and the account menu.
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
let shelfAnimation;
reducedMotion.addEventListener("change", () => shelfAnimation?.cancel());
function animateShelf() {
  shelfAnimation?.cancel();
  if (!rail.animate) return;
  shelfAnimation = rail.animate(
    reducedMotion.matches ? [{opacity: .8}, {opacity: 1}] :
      [{opacity: .65, clipPath: "inset(0 0 0 12px)"}, {opacity: 1, clipPath: "inset(0 0 0 0)"}],
    {duration: reducedMotion.matches ? 100 : 220, easing: "cubic-bezier(.16,1,.3,1)"}
  );
}
const rail = document.querySelector("#book-rail");
if (rail) {
  const chips = document.querySelectorAll("[data-filter]");
  const controls = document.querySelectorAll("[data-scroll]");
  function updateControls() {
    controls.forEach(button => {
      button.disabled = Number(button.dataset.scroll) < 0
        ? rail.scrollLeft < 2
        : rail.scrollLeft + rail.clientWidth >= rail.scrollWidth - 2;
    });
  }
  chips.forEach(chip => chip.addEventListener("click", () => {
    if (chip.getAttribute("aria-pressed") === "true") return;
    chips.forEach(other => {
      other.classList.toggle("active", other === chip);
      other.setAttribute("aria-pressed", String(other === chip));
    });
    rail.querySelectorAll("[data-category]").forEach(card => {
      card.hidden = Boolean(chip.dataset.filter && card.dataset.category !== chip.dataset.filter);
    });
    rail.scrollLeft = 0;
    updateControls();
    animateShelf();
  }));
  controls.forEach(button => button.addEventListener("click", () => {
    rail.scrollBy({left: Number(button.dataset.scroll) * rail.clientWidth,
      behavior: reducedMotion.matches ? "instant" : "smooth"});
  }));
  rail.addEventListener("scroll", updateControls, {passive:true});
  new ResizeObserver(updateControls).observe(rail);
  updateControls();
}
document.querySelectorAll("form[data-confirm]").forEach(form => {
  form.addEventListener("submit", event => {
    if (!window.confirm(form.dataset.confirm)) event.preventDefault();
  });
});
document.querySelectorAll("input[name=seat]").forEach(seat => {
  seat.addEventListener("change", () => {
    const choice = document.getElementById("seat-choice");
    if (!choice) return;
    choice.textContent = `Seat ${seat.dataset.label} selected.`;
  });
});

// Show submission feedback after validation and any confirmation has succeeded.
document.addEventListener("submit", event => {
  const form = event.target;
  if (event.defaultPrevented) return;
  if (form.dataset.submitting) {
    event.preventDefault();
    return;
  }
  form.dataset.submitting = "true";
  form.setAttribute("aria-busy", "true");
  event.submitter?.classList.add("submitting");
});
window.addEventListener("pageshow", () => {
  document.querySelectorAll("[data-submitting]").forEach(form => {
    delete form.dataset.submitting;
    form.removeAttribute("aria-busy");
  });
  document.querySelectorAll(".submitting").forEach(button => button.classList.remove("submitting"));
});

document.addEventListener("click", event => {
  document.querySelectorAll(".account-menu[open]").forEach(menu => {
    if (!menu.contains(event.target)) menu.open = false;
  });
});
document.addEventListener("keydown", event => {
  if (event.key !== "Escape") return;
  const menu = document.querySelector(".account-menu[open]");
  if (menu) {
    menu.open = false;
    menu.querySelector("summary").focus();
  }
});
document.querySelectorAll('input[name="phone"]').forEach(input => {
  function validatePhone() {
    const value = input.value.trim();
    const valid = !value || (/^\+?[0-9() .-]+$/.test(value) &&
      (value.match(/[0-9]/g) || []).length >= 7 && (value.match(/[0-9]/g) || []).length <= 15);
    input.setCustomValidity(valid ? "" : "Enter a phone number with 7–15 digits. A leading + is allowed.");
  }
  input.addEventListener("input", validatePhone);
  validatePhone();
});

const regionInput = document.querySelector('input[name="region"]');
const citySuggestions = document.querySelector("#address-cities");
if (regionInput && citySuggestions) {
  const cityOptions = Array.from(citySuggestions.options).map(option => ({
    value: option.value, region: option.dataset.region
  }));
  function updateCities() {
    const selected = regionInput.value.trim().toLowerCase();
    citySuggestions.replaceChildren(...cityOptions
      .filter(option => !selected || option.region.toLowerCase() === selected)
      .map(option => new Option(option.value, option.value)));
  }
  regionInput.addEventListener("input", updateCities);
  updateCities();
}
