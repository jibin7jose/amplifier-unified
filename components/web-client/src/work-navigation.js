import {createContext} from 'react';
// Shared presentation controller. Native sessions remain the history authority.
export const WorkNavigationContext=createContext(null);
export const workSurface=state=>state?.view?.workSurface||'chat';
export const browsePatch=(surface,workspaceId=null)=>({workSurface:surface,workWorkspaceId:workspaceId,workWorkspaceTab:'chats'});
