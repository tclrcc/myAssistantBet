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

/* Filtre de tableau, purement local : le catalogue complet des competitions
   fait pres de deux cents lignes, et un aller-retour serveur pour masquer des
   lignes deja rendues serait du gaspillage. */
document.addEventListener("input", (event) => {
  const champ = event.target.closest("[data-filter-rows]");
  if (!champ) return;

  const table = document.querySelector(champ.dataset.filterRows);
  if (!table) return;

  const terme = champ.value.trim().toLowerCase();
  let visibles = 0;
  table.querySelectorAll("tbody tr").forEach((ligne) => {
    const trouve = !terme || ligne.textContent.toLowerCase().includes(terme);
    ligne.hidden = !trouve;
    if (trouve) visibles += 1;
  });

  const compteur = document.querySelector(champ.dataset.filterCount);
  if (compteur) compteur.textContent = visibles;
});
