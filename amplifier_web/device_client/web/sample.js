let count=0;
document.querySelector('#sample').addEventListener('submit',event=>{
 event.preventDefault();
 const name=document.querySelector('#name').value, team=document.querySelector('input[name=team]:checked')?.value;
 document.querySelector('#saved').textContent=name&&team?`Saved ${name} ${team}.`:'Choose a name and team.';
 if(name&&team)document.querySelector('#count').textContent=String(++count);
});
