import React,{createContext,useContext,useEffect,useState} from 'react';

// Namespaced host-context extension; other MCP Apps can ignore it.
export const MCP_VISIBILITY='com.microsoft.amplifier/visibility';
const VisibilityContext=createContext(true);
export function McpAppVisibilityProvider({visible,children}){
 return <VisibilityContext.Provider value={visible}>{children}</VisibilityContext.Provider>;
}
export function useMcpAppVisibility(open){
 const canvasVisible=useContext(VisibilityContext);
 const [documentVisible,setDocumentVisible]=useState(()=>document.visibilityState!=='hidden');
 useEffect(()=>{
  const change=()=>setDocumentVisible(document.visibilityState!=='hidden');
  document.addEventListener('visibilitychange',change);
  return()=>document.removeEventListener('visibilitychange',change);
 },[]);
 return open!==false&&canvasVisible&&documentVisible;
}
