import React,{createContext,useContext} from 'react';

const McpAppThemeContext=createContext('system');

export function McpAppThemeProvider({scheme,children}){
 return <McpAppThemeContext.Provider value={scheme||'system'}>{children}</McpAppThemeContext.Provider>;
}

export function useMcpAppTheme(){
 return useContext(McpAppThemeContext);
}
