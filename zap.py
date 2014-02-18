#ZAP - Zurich Atmosphere Purge
#Developed by Kurt Soto 
import numpy as np
#import pyfits
from astropy.io import fits as pyfits
from time import time
from scipy import ndimage
from multiprocessing import Pool
import multiprocessing
import functools
import matplotlib.pyplot as plt
import os


##################################################################################################
################################### Top Level Functions ##########################################
##################################################################################################

def process(musecubefits, outcubefits='DATACUBE_FINAL_ZAP.fits', clean=True, zlevel=True, 
            cfilter=100, pevals=[], nevals=[], optimize=False, silent=False):
    """
    Performs the entire ZAP sky subtraction algorithm on an input fits file and writes the 
    product to an output fits file. 
 
    """
    #check if outcubefits exists before beginning 
    if os.path.exists(outcubefits):
        print 'output filename "{0}" exists'.format(outcubefits) 
        return

    hdu = pyfits.open(musecubefits)
    
    zobj = zclass(hdu[1].data, hdu[1].header)

    zobj._run(clean=clean, zlevel=zlevel, cfilter=cfilter, 
               pevals=pevals, nevals=nevals, optimize=optimize, silent=silent)

    
    hdu[1].data = zobj._cubetowrite()
    hdu[1].header = _newheader(zobj)

    hdu.writeto(outcubefits)

    hdu.close()


def _dev(cube, header, clean=True, zlevel=True, cfilter=100, pevals=[], 
                nevals=[], optimize=False, silent=False):
    """
    Developer mode.  For inserting small cubes.
 
    """
    
    #create an instance 
    zobj=zclass(cube, header)

    zobj._run(clean=clean, zlevel=zlevel, cfilter=cfilter, 
               pevals=pevals, nevals=nevals, optimize=optimize, silent=silent)
    
    return zobj


def interactive(musecubefits, clean=True, zlevel=True, cfilter=100, pevals=[], 
                nevals=[], optimize=False, silent=False):
    """
    Performs the entire ZAP sky subtraction algorithm on an input datacube and header. A class 
    containing all of the necessary data to examine the result and modify as desired.
 
    """
    
    #create an instance 

    hdu = pyfits.open(musecubefits)
    zobj= zclass(hdu[1].data, hdu[1].header)

    zobj._run(clean=clean, zlevel=zlevel, cfilter=cfilter, 
               pevals=pevals, nevals=nevals, optimize=optimize, silent=silent)
    
    return zobj


##################################################################################################
##################################### Process Steps ##############################################
##################################################################################################

class zclass:

    """
    The zclass retains all methods and attributes to run each of the steps of ZAP. 

    Attributes:

      cleancube - The final datacube after removing all of the residual features.

      contarray - A 2d array containing the subtracted continuum per spaxel.

      cube - The original data cube with the zlevel subtraction performed per spaxel.
    
      especeval - A list containing the full set of eigenspectra and eigenvalues generated by the 
                     SVD calculation that is used to reconstruct the entire datacube.

      laxis - A 1d array containing the wavelength solution generated from the header 
                     parameters.

      lparams - An array of parameters taken from the header to generate the wavelength solution.

      lranges - A list of the wavelength bin limits used in segmenting the sepctrum for SVD.

      lmin,lmax - the wavelength limits placed on the datacube

      nancube - A 3d boolean datacube containing True in voxels where a NaN value was replaced 
                     with an interpolation.

      nevals - A 1d array containing the number of eigenvalues used per segment to reconstruct 
                     the residuals.

      normstack - A normalized version of the datacube decunstructed into a 2d array.

      nsegments - The number of divisions in wavelength space that the cube is cut into in order 
                     to perform the SVD.

      varlist - An array for each segment with the variance curve, calculated for the 
                    optimize method.

      pranges - The pixel indices of the bounding regions for each spectral segment.

      recon - A 2d array containing the reconstructed emission line residuals.

      run_clean - Boolean that indicates that the NaN cleaning method was used.

      run_zlevel - Boolean indicating that the zero level correction was used.

      stack - The datacube deconstructed into a 2d array for use in the the SVD.

      subespeceval - The subset of eigenvalues and eigenspectra used to reconstruct the sky 
                        residuals.

      variancearray - A list of length nsegments containing variances calculated per spaxel used
                        for normalization

      y,x - The position in the cube of the spaxels that are in the 2d deconstructed stack

      zlsky - A 1d array containing the result of the zero level subtraction

      

    """
    
    #setup the data structure    
    def __init__(self, cube, header):
        """
        Initialization of the zclass. Pulls the datacube into the class and trims it based 
        on the known optimal spectral range of MUSE.
        """
        
        self.header = header
        
        lparams = [header['NAXIS3'], header['CRVAL3'], header['CD3_3'], header['CRPIX3']]
        laxis=lparams[1] + lparams[2] * (np.arange(lparams[0]) + lparams[3] - 1)

        lmin = 4800
        self.lmin = lmin
        lmax = 9300
        self.lmax = lmax

        wlaxis = np.where(np.logical_and(laxis >= lmin, laxis <= lmax))[0]
        wlmin = min(wlaxis)
        wlmax = max(wlaxis)
        self._wlmin = wlmin
        self._wlmax = wlmax
        
        laxis = laxis[wlmin:wlmax+1]
        self.cubetrimb = cube[:wlmin,:,:] #save the trimmings
        self.cubetrimr = cube[wlmax+1:,:,:]

        cube = cube[wlmin:wlmax+1, :, :] #cut off the unusable bits
        self.cube = cube

        self.laxis = laxis

        #NaN Cleaning
        self.run_clean = False
        self.nancube = None
        self._boxsz = 1
        self._rejectratio = 0.25
        
        #zlevel parameters
        self.run_zlevel = False

        #Extraction results
        self.stack = np.array([])
        self.y = np.array([])
        self.x = np.array([])
        
        #Normalization Maps
        self.contarray = np.array([])
        self.variancearray = np.array([])
        self.normstack = np.array([])
        
        #SVD Results
        lranges=np.array([[0   ,5400],
                          [5400,5850],
                          [5850,6400],    
                          [6400,6700],    
                          [6700,7150],    
                          [7150,7700],    
                          [7700,8200],
                          [8200,8700],    
                          [8700,10000]]) 

        self.lranges=lranges
        self.nsegments = len(lranges)
        self.lparams = [header['NAXIS3'], header['CRVAL3'], header['CD3_3'], header['CRPIX3']]

        paxis=np.arange(len(laxis))
        pranges=[]
        for i in range(len(lranges)):
            lrangelogical= np.logical_and(laxis > lranges[i,0],laxis <= lranges[i,1])
            pranges.append((np.min(paxis[lrangelogical]),np.max(paxis[lrangelogical])+1))

        self.pranges=np.array(pranges)

        self.especeval = []
        
        #eigenspace Subset
        self.subespeceval = []

        #Reconstruction of sky features
        self.recon = np.array([])
        self.cleancube = np.array([])
        self.varlist = np.array([]) #container for variance curves


    def _run(self, clean=True, zlevel=True, cfilter=100, pevals=False, nevals=False, 
            optimize=False, calctype='median', nsig=3., q=4, silent=False):
	
        """
        
        Perform all zclass to ZAP a datacube, including NaN re/masking, deconstruction into 
        "stacks", zerolevel subraction, continuum removal, normalization, singular value 
        decomposition, eigenvector selection, residual reconstruction and subtraction, and 
        data cube reconstruction.
	
        Returns a "zclass" class that retains all of the data needed as the routine progresses.
	
        """
	
        t0=time()
		
        # clean up the nan values
        if clean != False:
            self._nanclean()
            
        # Extract the spectra that we will be working with
        self._extract()
        
        #remove the median along the spectral axis
        if zlevel == True:
            self._zlevel(calctype=calctype, nsig=nsig, q=q)
	    
        #remove the continuum level - this is multiprocessed to speed it up
        self._continuumfilter(cfilter=cfilter)
	
        # do the multiprocessed SVD calculation
        self._msvd()
	
        # choose some fraction of eigenspectra or some finite number of eigenspectra
        if optimize == True:
            self.optimize()
        else:
            self.chooseevals(pevals=pevals, nevals=nevals)
            self.reconstruct() # reconstruct the sky residuals using the subset of eigenspace 
	
        # stuff the new spectra back into the cube
        self.remold()
	            
        t=time()-t0
        print 'Time to Fully processs: {0}'.format(int(t))


##Identifies spaxels without any problem nan pixels
##output a stack of spectra and arrays with the coordinates
    def _zlevel(self, calctype='median', nsig=3., q=4):
        """

        Removes a 'zero' level from each spectral plane. Spatial information is not required,
        so it operates on the extracted stack.

        Operates on stack, leaving it with this level removed and adds the data 'zlsky' to the 
        class. zlsky is a spectrum of the zero levels.
        
        This zero level is currently calculated with a median.

        Experimental operations -
        
        - exclude top quartile
        - run in an iterative sigma clipped mode

        """

        print 'Subtracting zero level'
        t0=time()

        self.zlsky=np.median(np.median(self.cube,axis=-1),axis=-1) 
        self.stack = self.stack - self.zlsky[:,np.newaxis]

        self.run_zlevel = True
        t = time() - t0
        print 'Time to remove zero level: {0} s'.format(int(t))

    def _extract(self, silent=False):
        """
        Deconstruct the datacube into a 2d array, since spatial information is not required, and
        the linear algebra routines require 2d arrays.

        The operation rejects any spaxel with even a single NaN value, since this would cause the
        linear algebra routines to crash.
        
        Adds the x and y data of these positions into the zclass

        """
        if silent == False:
            print 'Extracting to 2D'

        # make a map of spaxels with NaNs
        badmap=(np.logical_not(np.isfinite(self.cube))).sum(axis=0) 
        self.y,self.x=np.where(badmap == 0)    # get positions of those with no NaNs
        self.stack=self.cube[:,self.y,self.x]  # extract those positions into a 2d array
            
    ## Clean up the nan value spaxels
    def _nanclean(self, silent=False):
        """
        Detects NaN values in cube and removes them by replacing them with an 
        interpolation of the nearest neighbors in the data cube. The positions in the cube 
        are retained in nancube for later remasking.
        """
        boxsz = self._boxsz
        rejectratio = self._rejectratio 
        t0=time()
        
        cleancube = self.cube.copy()                #        
        badcube = np.logical_not(np.isfinite(cleancube))        # find NaNs 
        badmap = (badcube).sum(axis=0)  # map of total nans in a spaxel
        
    # choose some maximum number of bad pixels in the spaxel and extract positions
        y, x = np.where(badmap > (rejectratio*(cleancube.shape)[0]))
        nbadspax = len(y)
        print "{0} spaxels rejected: > {1}% NaN pixels".format(nbadspax, rejectratio * 100)
        
    # make cube mask of bad spaxels
        bcube=np.ones(cleancube.shape, dtype=bool)
        bcube[:,y,x] = False
        
        badcube=np.logical_and(badcube == True, bcube == True) # combine masking
        z, y, x = np.where(badcube)
        
        neighbor=np.zeros((z.size,(2*boxsz+1)**3))
        icounter=0
        print "fixing {0} pixels".format(len(z))
            
    #loop over samplecubes
        for j in range(-boxsz,boxsz+1,1):
            for k in range(-boxsz,boxsz+1,1):
                for l in range(-boxsz,boxsz+1,1):
                    iz, iy, ix = z+l, y+k, x+j
                    outx=np.logical_or(ix <= 0, ix >= (cleancube.shape)[2]-1)
                    outy=np.logical_or(iy <= 0, iy >= (cleancube.shape)[1]-1)
                    outz=np.logical_or(iz <= 0, iz >= (cleancube.shape)[0]-1)
                    outsider=np.where(np.logical_or(np.logical_or(outx,outy), outz))
                    ix[ix < 0] = 0 ; ix[ix > (cleancube.shape)[2]-1] = (cleancube.shape)[2]-1
                    iy[iy < 0] = 0 ; iy[iy > (cleancube.shape)[1]-1] = (cleancube.shape)[1]-1
                    iz[iz < 0] = 0 ; iz[iz > (cleancube.shape)[0]-1] = (cleancube.shape)[0]-1
                    neighbor[:,icounter]=cleancube[iz,iy,ix]
                    neighbor[outsider,icounter] = np.nan
                    icounter=icounter+1
        goodneighbor = np.isfinite(neighbor)
        neighbor[np.logical_not(goodneighbor)]=0
        nfix=goodneighbor.sum(axis=1)
        tfix= neighbor.sum(axis=1)
        neighborless = nfix == 0
        fix=np.zeros(z.size)
        fix[neighborless]=np.nan
        fix[np.logical_not(neighborless)] = \
            tfix[np.logical_not(neighborless)]/nfix[np.logical_not(neighborless)]
        cleancube[z,y,x]=fix
        
        t=time()-t0
        if silent == False:
            print 'Time to clean NaNs: {0} s'.format(int(t))
        
        self.run_clean = True
        self.cube = cleancube
        self.nancube = badcube
        #self.nanpos = [z,y,x]

    def _continuumfilter(self, cfilter=100, silent=False):

        """
        A multiprocessed implementation of the continuum removal. This process distributes the 
        data to many processes that then reassemble the data. Uses two filters, a small scale 
        (less than the line spread function) uniform filter, and a large scale median filter
        to capture the structure of a variety of continuum shapes.
        
        added to class
        contarray - the removed continuua
        normstack - "normalized" version of the stack with the continuua removed
        """

        t0=time()
        print 'Continuum Subtracting'
        self._cfilter = cfilter
        nmedpieces=16

        #define bins
        
        edges = np.append(np.floor(
            np.arange(0, self.stack.shape[1], self.stack.shape[1]/np.float(nmedpieces))), 
                          self.stack.shape[1])

        medianranges=np.array(zip(edges[0:-1],edges[1::])).astype(int)

        pool = Pool(processes = nmedpieces) # start pool

    #do the multiprocessing on each ministack and return medianarray
        meanpieces = pool.map(functools.partial(_icontinuumfilter, stack = self.stack, 
                                                cfilter=self._cfilter), medianranges)
        pool.close()

        self.contarray=np.concatenate(meanpieces, axis=1)

    #remove continuum features
        self.normstack = self.stack - self.contarray
        if silent == False:
            print 'Time to Subtract : {0} s'.format(int(time()-t0))    

    def _msvd(self, silent=False):

        """
        Multiprocessed singular value decomposition. 

        First the normstack is normalized per segment per spaxel by the variance.
        Takes the normalized, spectral segments and distributes them to the individual svd
        methods.

        """
        t0=time()
        
    #split the range
                
        nseg = len(self.pranges)
        
    #normalize the variance in the segments
        self.variancearray = np.zeros((nseg, self.stack.shape[1]))
        
        for i in range(nseg):
            self.variancearray[i,:] = np.var(self.normstack[
                self.pranges[i,0]:self.pranges[i,1], :], axis=0)
            self.normstack[self.pranges[i,0]:self.pranges[i,1], :] = self.normstack[
                self.pranges[i,0]:self.pranges[i,1], :] / self.variancearray[i,:]

        print 'Beginning SVD on {0} segments'.format(nseg) 
    # take each ministack and run them independently
        pool = Pool(processes=nseg) # start pool

        especeval = pool.map(functools.partial(_isvd, normstack = self.normstack), 
                             self.pranges)
        pool.close()
        pool.join()
        
        t=time()-t0
        if silent == False:
            print 'Time to run svd : {0} s'.format(int(t))    

        self.especeval = especeval
    
    def chooseevals(self, nevals=[], pevals=[]):
        """
        Choose the number of eigenspectra/evals to use for reconstruction
        
        user supplies the number of eigen spectra to be used (neval) or the percentage
        of the eigenspectra that were calculated (peval) from each spectral segment to be used. 
        
        The user can either provide a single value to be used for all segments, or
        provide an array that defines neval or peval per segment.
        
        """
        nranges = len(self.especeval)
        nevals = np.array(nevals)
        pevals = np.array(pevals)
        nespec=[]                                                              
        for i in range(nranges):                                               
            nespec.append((self.especeval[i][0]).shape[1])                     
        nespec=np.array(nespec)                                                

        #deal with no selection
        if len(nevals) == 0 and len(pevals) == 0:
            print 'number of modes not selected'
            nevals=np.array([1])
        
        #deal with an input list
        if len(nevals) >= 1:
            if len(nevals) != nranges:
                nevals = np.array([nevals[0]])
                print('Chosen eigenspectra array does not correspond to number of segments')
            else:
                print('Choosing {0} eigenspectra for segments'.format(nevals))
        
        if len(pevals) >= 1:
            if len(pevals) != nranges:
                pevals = np.array([pevals[0]])
                print('Chosen eigenspectra array does not correspond to number of segments')
            else:
                print('Choosing {0}% of eigenspectra for segments'.format(pevals))
                nevals = (pevals*nespec/100.).round().astype(int)
        
        # deal with single value entries
        if len(pevals) == 1:
            print('Choosing {0}% of eigenspectra for all segments'.format(pevals))
            nevals = (pevals*nespec/100.).round().astype(int)
        elif len(nevals) == 1:
            print('Choosing {0} eigenspectra for all segments'.format(nevals))
            nevals = np.zeros(nranges, dtype=int) + nevals
            
        #take subset of the eigenspectra and put them in a list
        subespeceval=[]
        for i in range(nranges):
            eigenspectra, evals = self.especeval[i]
            tevals = (evals[0:nevals[i],:]).copy()
            teigenspectra = (eigenspectra[:,0:nevals[i]]).copy()
            subespeceval.append((teigenspectra,tevals))
    
        self.subespeceval = subespeceval
        self.nevals = nevals

    def reconstruct(self):

        """
        Multiprocessed residual reconstruction.

        Distributes the trimmed eigenspectra/eigenvalues to the reconstruction method.
        """

        print 'Reconstructing Sky Residuals'    

        nseg=len(self.especeval)
    
        # take each ministack and run them independently
        pool = Pool(processes=nseg) # start pool

 
    
        #do the multiprocessing on each ministack and return eigenvalues/eigenvectors
        reconpieces = pool.map(_ireconstruct, self.subespeceval)

        pool.close()
        pool.join()
    
        #rescale to correct variance
        for i in range(nseg):
            reconpieces[i] = (reconpieces[i] * self.variancearray[i,:])
        self.recon=np.concatenate(reconpieces)
        
    
    #stuff the stack back into a cube
    def remold(self):
        """
        Subtracts the reconstructed residuals and places the cleaned spectra into the duplicated
        datacube.
        """
        print 'Reshaping data product'
        self.cleancube = self.cube.copy()
        self.cleancube[:,self.y,self.x] = self.stack-self.recon
        if self.run_clean == True:
            self.cleancube[self.nancube] = np.nan
            #self.cleancube[self.nanpos[0],self.nanpos[1],self.nanpos[2]]
    
    #redo the residual reconstruction with a different set of parameters
    def reprocess(self, pevals=False, nevals=False):
        """
        A method that redoes the eigenvalue selection, reconstruction, and remolding of the 
        data.
        """
        
        self.chooseevals(pevals=pevals, nevals=nevals)
        self.reconstruct()
        self.remold()


    def optimize(self):
        """
        Function to optimize the number of components used to characterize the residuals.

        This function calculates the variance per segment with an increasing number of 
        eigenspectra/eigenvalues. It then deterimines the point at which the second derivative 
        of this variance curve reaches zero. When this occurs, the linear reduction in variance
        is attributable to the removal of astronomical features rather than emission line 
        residuals.

        """
        print 'Optimizing'

        t0 = time()

        nseg=len(self.especeval)

        #for receiving results of processes
        manager = multiprocessing.Manager()
        return_dict = manager.dict()
        
        jobs = []

        #multiprocess the variance calculation, operating per segment
        for i in range(nseg): 
            p = multiprocessing.Process(target=_ivarcurve,args=(i,
                                                                self.stack,
                                                                self.pranges,
                                                                self.especeval,
                                                                self.variancearray,return_dict))
            jobs.append(p)
            p.start()
        
        #gather the results
        for proc in jobs:
            proc.join()

        self.varlist = np.array(return_dict.values())
        self.nevals=np.zeros(nseg, dtype=int)

        for i in range(nseg):

            deriv = (np.roll(self.varlist[i],-1)-self.varlist[i])[:-1] #calculate "derivative"
            deriv2 = (np.roll(deriv,-1)-deriv)[:-1]

            noptpix=self.varlist[i].size
            
            #statistics on the derivatives
            mn1=deriv[.75 * (noptpix-2):].mean() 
            std1=deriv[.75 * (noptpix-2):].std()
            mn2=deriv2[.75 * (noptpix-2):].mean()
            std2=deriv2[.75 * (noptpix-2):].std()            
            ind = np.arange(self.varlist[i].size) #for matching logicals to indices
            
            #look for crossing points. When they get within 3 sigma of scatter in settled region.
            cross1 = np.append(False, deriv >= mn1 - 2*std1) #pad by 1 for 1st deriv
            cross2 = np.append([False,False], deriv2 <= mn2 + 2*std2) #pad by 2 for 2nd
            
            cross = np.logical_or(cross1,cross2)
            
            self.nevals[i] = min(ind[cross])+1

        self.chooseevals(nevals=self.nevals)
        self.reconstruct()

        print 'Time to optimize: {0} s'.format(int(time()-t0))

    def _cubetowrite(self):
        return np.concatenate((self.cubetrimb,self.cleancube,self.cubetrimr), axis=0)

    def writecube(self, outcubefits='DATACUBE_ZAP.fits'):
        """
        write the processed datacube to an individual fits file.
        """

        if os.path.exists(outcubefits):
            print 'output filename exists' 
            return

        #fix up for writing 
        outcube = self._cubetowrite()
        outhead = _newheader(self)
        
        #create hdu and write
        outhdu = pyfits.PrimaryHDU(data=outcube,header=outhead)
        outhdu.writeto(outcubefits)


    def mergefits(self, musecubefits):
        """
        Merge the ZAP cube into the full muse datacube
        """
        if os.path.exists(musecubefits):
            print 'output filename exists' 
            return
            
        hdu = pyfits.open(musecubefits)
        hdu[1].header = _newheader(zclass)
        hdu[1].data = self._cubetowrite()
        


##################################################################################################
##################################### Helper Functions ###########################################
##################################################################################################


##### Continuum Filtering #####
def _icontinuumfilter(sprange, stack, cfilter):  #for distributing data to the pool
    """
    Helper function to distribute data to Pool for SVD
    """
    ufilt=3 #set this to help with extreme over/under corrections
    result = ndimage.median_filter(
        ndimage.uniform_filter(stack[:,sprange[0]:sprange[1]], (ufilt,1)), (cfilter,1))
    return result

##### SVD #####
def _isvd(prange, normstack, silent=False): #for distributing data to the pool
    """
    Perform single value decomposition and Calculate PC amplitudes (projection)
    outputs are eigenspectra operates on a 2D array.

    eigenspectra = [nbins, naxes]
    evals = [naxes, nobj]
    data = [nbins, nobj]
    """
 
    inormstack=normstack[prange[0]:prange[1],:]
    
    inormstack=np.transpose(inormstack)
    
    U,s,V=np.linalg.svd(inormstack, full_matrices=0)
    eigenspectra = np.transpose(V)
    evals=(inormstack).dot(np.transpose(V))
    evals=np.transpose(evals)
    
    if silent == False:
        print 'Finished SVD Segment'
                
    return eigenspectra, evals


##### RECONSTRUCTION  #####
def _ireconstruct(iespeceval):  
    """
    Reconstruct the residuals from a given set of eigenspectra and eigenvalues
    """

    eigenspectra, evals = iespeceval
    nrows=(evals.shape)[1]
    reconpiece=np.zeros([(eigenspectra.shape)[0],nrows]) # make container
    for i in np.arange(nrows): # this loop is FASTER than a fully vectorized one-liner command
        evalvect=(evals[:,i]) # choose single eval set
        reconpiece[:,i]=np.sum(eigenspectra * evalvect,axis=1) # broadcast evals on evects and sum

    return reconpiece

def _ipreconstruct(i, iespeceval, precon):  
    """
    Reconstruct the residuals from a given set of eigenspectra and eigenvalues.

    this is a special version for caculating the variance curve. It adds the contribution of a 
    single mode to an existing reconstruction.

    """

    eigenspectra, evals = iespeceval
    eigenspectra = eigenspectra[:,i]
    evals = evals[i,:]
    reconpiece = precon + (eigenspectra[:,np.newaxis] * evals[np.newaxis,:]) # broadcast evals on evects and sum

    return reconpiece


##### OPTIMIZE #####
def _ivarcurve(i, stack, pranges, especeval, variancearray,return_dict):

    #segment the data
    istack = stack[pranges[i,0]:pranges[i,1],:]
    iprecon = np.zeros_like(istack)
    iespeceval = especeval[i]
    ivariancearray = variancearray[i]
    ivarlist = []
    totalnevals = int(np.round((iespeceval[1].shape[0])*0.20))

    for nevals in range(1,totalnevals):
        if nevals % (totalnevals * .1) <= 1:
            print 'Seg {0}: {1}% complete '.format(i, int(nevals/(totalnevals-1.)*100.))
        iprecon = _ipreconstruct(nevals, iespeceval, iprecon)
        icleanstack = istack - (iprecon * ivariancearray)
        ivarlist.append(np.var(icleanstack)) #calculate the variance on the cleaned segment
    
    ivarlist = np.array(ivarlist)
    return_dict[i] = ivarlist

def _newheader(zclass):

    header=zclass.header.copy()

    #put the pertinent zap parameters into the header
    header['COMMENT']='These data have been ZAPped!'

    # zlevel removal performed 
    header.append(('ZAPzlvl',zclass.run_zlevel, 'ZAP zero level correction performed'), end=True)

    # Nanclean performed
    header['ZAPclean'] = (zclass.run_clean, 'ZAP NaN cleaning performed for calculation')
    
    # Continuum Filtering
    header['ZAPcfilt'] = (zclass._cfilter, 'ZAP continuum filter size')

    # number of segments
    nseg=len(zclass.pranges)
    header['ZAPnseg'] = (nseg, 'Number of segments used for ZAP SVD')

    # per segment variables
    for i in range(nseg):
        header['ZAPpseg{0}'.format(i)] = ('{0}:{1}'.format(zclass._wlmin+zclass.pranges[i][0], zclass._wlmin+zclass.pranges[i][1]-1), 'spectrum segment (pixels)')
        header['ZAPnev{0}'.format(i)] = (zclass.nevals[i], 'number of eigenvals/spectra used')
    
    return header

def plotvarcurve(zobj, i=0):

    if len(zobj.varlist) == 0:
        print 'No varlist found. The optimize method must be run first. \n'
        return

    varcurve=zobj.varlist[i]

    deriv = (np.roll(zobj.varlist[i],-1)-zobj.varlist[i])[:-1] #calculate "derivative"
    deriv2 = (np.roll(deriv,-1)-deriv)[:-1]
    
    noptpix=zobj.varlist[i].size
    
    #statistics on the derivatives
    mn1=deriv[.75 * (noptpix-2):].mean() 
    std1=deriv[.75 * (noptpix-2):].std()
    mn2=deriv2[.75 * (noptpix-2):].mean()
    std2=deriv2[.75 * (noptpix-2):].std()            
    
    fig=plt.figure(figsize=[10,15])
    ax= fig.add_subplot(3,1,1)
    plt.plot(varcurve)

    ax= fig.add_subplot(3,1,2)
    plt.plot(np.arange(deriv.size)+1,deriv)
    plt.plot([1,noptpix-1],[mn1,mn1])
    plt.plot([1,noptpix-1],[mn1-3*std1,mn1-3*std1])

    ax= fig.add_subplot(3,1,3)
    plt.plot(np.arange(deriv.size)+2,deriv2)
    plt.plot([2,noptpix-2],[mn2,mn2])
    plt.plot([2,noptpix-2],[mn2-3*std2,mn2-3*std2])
    plt.plot([2,noptpix-2],[mn2+3*std2,mn2+3*std2])
    plt.suptitle(i)
