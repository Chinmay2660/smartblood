document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll('a[href^="#"]').forEach((link) => {
    link.addEventListener("click", () => {
      const target = document.querySelector(link.getAttribute("href"));
      if (target) target.classList.add("visited-section");
    });
  });
  document.querySelectorAll("form").forEach((form) => {
    form.addEventListener("submit", () => {
      const button = form.querySelector('button[type="submit"]');
      if (button && !form.hasAttribute("data-no-loading")) {
        button.dataset.originalText = button.textContent;
        button.style.opacity = "0.75";
      }
    });
  });
});
