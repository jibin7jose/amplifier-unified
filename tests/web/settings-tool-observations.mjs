export async function settingsToolObservations(page){
 const response=await page.request.get(new URL('/fixture/settings-tool-observations',page.url()).href);
 if(!response.ok())throw new Error('Settings tool observations failed: '+response.status());
 return response.json();
}
