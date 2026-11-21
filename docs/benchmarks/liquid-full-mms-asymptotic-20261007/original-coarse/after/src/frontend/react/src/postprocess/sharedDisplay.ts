import { PostprocessDisplayDriver } from './displaySession'
import { createThreePostprocessAdapter } from '../renderer/threePostprocessAdapter'
/** Only view state: never changes .cfdproj/.cfdrun or numerical inputs. */
export const sharedPostprocessDisplay = new PostprocessDisplayDriver(createThreePostprocessAdapter())
