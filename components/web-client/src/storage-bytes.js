// Keep file-content displays consistent across the Storage settings pages.
export const formatStorageBytes=value=>value<1024?`${value} B`:value<1048576?`${(value/1024).toFixed(1)} KB`:value<1073741824?`${(value/1048576).toFixed(1)} MB`:`${(value/1073741824).toFixed(2)} GB`;
