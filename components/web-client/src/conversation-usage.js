export function usageMetric(metric, currency=false,compact=false){
 if(!Number.isFinite(metric?.value)||metric.value<0)return '';
 const value=metric.value.toLocaleString(undefined,{maximumFractionDigits:currency?(compact?(metric.value>=1?2:metric.value>=.01?3:6):6):0});
 const notes=[];
 if(metric.estimatedCalls>0)notes.push('includes estimates');
 return `${currency?'$':''}${value}${notes.length?' · '+notes.join(' · '):''}`;
}
