/* Le peu de JavaScript que l'application s'autorise : du vanilla, aucun
   framework ni build step (SPEC.md section 9). Tout le reste passe par HTMX.

   Une case « tout cocher » ne peut pas se faire cote serveur : ce serait un
   aller-retour pour un geste purement local a la page. */

document.addEventListener("click", (event) => {
  const master = event.target.closest("[data-check-all]");
  if (!master) return;

  const scope = master.closest("form") || document;
  const boxes = scope.querySelectorAll(`input[type="checkbox"][name="${master.dataset.checkAll}"]`);
  boxes.forEach((box) => {
    box.checked = master.checked;
  });
});

/* La case maitresse reflete l'etat reel : cocher puis decocher une ligne doit
   la faire retomber, sinon elle ment sur ce qui est selectionne. */
document.addEventListener("change", (event) => {
  const box = event.target;
  if (box.type !== "checkbox" || box.hasAttribute("data-check-all")) return;

  const scope = box.closest("form");
  if (!scope) return;
  const master = scope.querySelector(`[data-check-all="${box.name}"]`);
  if (!master) return;

  const boxes = scope.querySelectorAll(`input[type="checkbox"][name="${box.name}"]`);
  const cochees = Array.from(boxes).filter((item) => item.checked).length;
  master.checked = cochees === boxes.length;
  master.indeterminate = cochees > 0 && cochees < boxes.length;
});
