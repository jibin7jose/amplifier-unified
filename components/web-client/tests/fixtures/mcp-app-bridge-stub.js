export class AppBridge{
 constructor(...args){this.args=args;this.contexts=[];globalThis.mcpTestBridges.push(this)}
 async connect(){}
 setHostContext(value){this.contexts.push(value)}
 async close(){this.closed=true}
 async sendToolListChanged(){this.toolListChanges=(this.toolListChanges||0)+1}
 async sendResourceListChanged(){this.resourceListChanges=(this.resourceListChanges||0)+1}
 async sendToolInput(){}
 async sendToolResult(){}
}
export class PostMessageTransport{}
