'''
Makes Copernicus CDS API requests.
'''
import zipfile
from enum import Enum
from pathlib import Path

from dataclasses import dataclass, field

import cdsapi

import climate_data.copernicus.cmip6 as cmip6

class Status(Enum):
    '''Request status.'''
    ERROR = 'error'
    SUCCESS = 'success'
    UNPROCESSED = 'unprocessed'

@dataclass
class CMIP6Request:
    '''
    Copernicus CMIP6 data request.
        single: experiment, model, location, variable, time_step, months, days.

    Note:
        Years are inferred from the experiment.
        Multiple experiments, models, variables, and/or locations are NOT allowed.
        TODO: Multiple levels (of atmosphere) are not yet implemented.
    '''
    model: cmip6.Models = cmip6.Models.ACCESS_CM2
    experiment: cmip6.Experiments = cmip6.Experiments.HISTORICAL
    years: None|tuple[str,...] = None # inferred from experiment

    location: tuple[int, int, int, int] = field(default=(1, 0, 0, 1)) # [N, W, S, E]

    variable: cmip6.Variables = cmip6.Variables.TEMP
    time_step: str = cmip6.TemporalResolutions.MONTHLY
    months: tuple[str, ...] = cmip6.MONTHS
    days: None|tuple[str, ...] = None

    def __post_init__(self):
        if self.time_step == cmip6.TemporalResolutions.FIXED:
            raise NotImplementedError('Fixed resolution not yet implemented.')
        if self.days is not None and self.time_step != cmip6.TemporalResolutions.DAILY:
            raise ValueError('Days are only valid for daily resolution.')
        possible_years = cmip6.default_years(self.experiment)
        if self.years is None:
            self.years = possible_years
        else: # validate years
            if not all([y in possible_years for y in self.years]):
                raise ValueError(f'Invalid years: {self.years}')
        self.status: Status = Status.UNPROCESSED
        self.file_chain: list[str] = []

    @property
    def request(self) -> dict[str, any]:
            # def _get_request(self) -> dict[str, any]:
        '''Returns a dictionary request.'''
        request = {
            "variable": self.variable.value,
            "temporal_resolution": self.time_step.value,
            "month": self.months,
            "area": self.location,
            "model": self.model.value,
            "experiment": self.experiment.value,
            "year": self.years,
        }
        if self.time_step == cmip6.TemporalResolutions.DAILY:
            request['day'] = self.days
        return request

    def create_directories(self, base_directory: str) -> str:
        '''
        Creates directory for CMIP6 data, at:
            base_directory/cmip6/variable/resolution/zip 
        '''
        path = Path(base_directory)
        if not path.exists():
            raise FileNotFoundError(
                f'Base directory: {path} does not exist.')
        # cmip6/variable/resolution (e.g. cmip6/tas/monthly)
        for part in ('cmip6', self.variable.value, self.time_step.value):
            path = path / part
            if not path.exists():
                print(f'making directory at: {path}')
                path.mkdir()
        return path

    def name_file(self) -> str:
        '''Creates a file name stem.'''
        def date(start: bool) -> str:
            i = 0 if start else -1
            return f'{self.years[i]}{self.months[i]}{self.days[i] if self.days else ""}'
        return f'{self.model.value}_{self.experiment.value}_{date(True)}-{date(False)}'

    def create_or_name_file(self, file_name: str = '',
                            file_format: str = cmip6.FileFormats.NETCDF.value) -> str:
        '''Validates file names, or creates on if one is not provided.'''
        if file_name:
            if Path(file_name).suffix == '':
                return f'{file_name}{file_format}'
            if Path(file_name).suffix != file_format:
                print(f'''Note: file: {file_name} has {Path(file_name).suffix} extension.
                      Specified format extension: {file_format} will be appended to file name.''')
                return f'{file_name}{file_format}'
        else: # create file name
            return f'{self.name_file()}{file_format}'

    def download(self, directory: str, file_name: str = '', overwrite: bool = False,
                 file_format: str = cmip6.FileFormats.NETCDF.value) -> str:
        '''
        Sends a request to the Copernicus CDS API.
        Stores the downloaded file in the specified directory.
        '''
        # validate and set file name.
        file_name = self.create_or_name_file(file_name, cmip6.FileFormats.ZIP.value)

        # validate directory and file path.
        filepath = Path(directory) / file_name
        if filepath.exists():
            if not overwrite:
                self.status = Status.ERROR
                raise FileExistsError(
                    f'''File at: {str(filepath)} already exists,
                    choose overwrite=True to replace existing file.''')
            filepath.unlink() # remove existing file.
        else:
            if not filepath.parent.exists():
                self.status = Status.ERROR
                raise FileNotFoundError(
                    f'Directory at: {str(filepath.parent)} not found.')

        # request data from CDS
        self.request['format'] = file_format
        try:
            client = cdsapi.Client()
            client.retrieve(cmip6.DATASET, self.request, filepath)
            self.file_chain.append(filepath)
            self.status = Status.SUCCESS
            #self.unzip_file(self.file_chain[-1], Path(file_name).stem, file_format, overwrite)
            #return str(self.file_chain[-1])
        except Exception as e: # pylint: disable=broad-except
            self.status = Status.ERROR
            return f'Error: {e}'
        self.unzip_file(self.file_chain[-1], Path(file_name).stem, file_format, overwrite)
        return str(self.file_chain[-1])

    def unzip_file(self, zippath: str, file_name: str = '',
                   file_format: str = cmip6.FileFormats.NETCDF.value,
                   overwrite: bool = False) -> str:
        '''Unzips a single file with a specified extension.'''          
        #check zip file path.
        if not Path(zippath).exists():
            self.status = Status.ERROR
            raise FileNotFoundError(f'Error: {zippath} not found.')
        if self.status != Status.SUCCESS:
            print(f'''Warning: unzip proceeding at {zippath},
                  but request status is {self.status}.''')

        # Validate and set file name.
        file_name = self.create_or_name_file(file_name, file_format)

        # Unzip file.
        new_name = Path(zippath).parent / file_name
        with zipfile.ZipFile(zippath, 'r') as zip_ref:
            files = [f for f in zip_ref.namelist() if f.endswith(file_format)]
            if len(files) == 0:
                self.status = Status.ERROR
                raise FileNotFoundError(
                    f'No {file_format} file found in {zippath}.')
            if len(files) != 1:
                self.status = Status.ERROR
                raise FileNotFoundError(
                    f'''Expected one {file_format} file,
                    found {len(files)} in {zippath}.''')
            if new_name.exists():
                if not overwrite:
                    self.status = Status.ERROR
                    raise FileExistsError(
                        f'''File at: {str(new_name)} already exists,
                        choose overwrite=True to replace.''')
                new_name.unlink()
            zip_ref.extract(files[0], Path(zippath).parent)
            (Path(zippath).parent / Path(files[0]).name).rename(new_name)
            self.file_chain.append(new_name)
        return new_name

def build_CMIP6Requests(location: tuple[int, int, int, int], # pylint: disable=invalid-name
                        variables: list[cmip6.Variables],
                        timesteps: list[cmip6.TemporalResolutions],
                        models: list[cmip6.Models],
                        experiments: list[cmip6.Experiments],
                        years: None|tuple[str,...] = None,
                        ) -> list[CMIP6Request]:
    '''
    Builds a list of CMIP6 requests for a single location.
    
    Note:
        [1] If years are not provided, they are inferred for each experiment.
        [2] Month and day (when applicable) request parameters are set to defaults,
            i.e., all months and days.
        [3] There are many possible error states, most are not checked until the request is made.
    '''
    if len(variables) != len(timesteps):
        raise ValueError('Variables and timesteps must have the same length.')
    requests = []
    for i, var in enumerate(variables):
        ts = timesteps[i]
        for model in models:
            for ssp in experiments:
                requests.append(CMIP6Request(model, ssp, years, location, var, ts))
    return requests

def download_requests(requests: list[CMIP6Request],
                      base_directory: str, overwrite: bool = False,
                      file_format: str = cmip6.FileFormats.NETCDF.value) -> list[str]:
    '''
    Bath process a list of CMIP6 requests.
    
    Note:
        [1] Downloaded files are given default names.
        [2] Default directory structure is created (in base directory).
    '''
    success_count = 0
    print(f'Downloading {len(requests)} requests to: {base_directory}')
    for i, r in enumerate(requests):
        r.download(r.create_directories(base_directory),
                   overwrite=overwrite, file_format=file_format)
        print(f'''    {[i]} {r.status}: {r.file_chain[-1].name}''')
        if r.status == Status.SUCCESS:
            success_count += 1
    print(f'Successfully processed {success_count} of {len(requests)} requests.')

@dataclass
class CMIP6Experiment:
    '''
    Copernicus CMIP6 experiment request.
    
        multiple: models.
        single: experiment, location, variable, time_step, months, days.
    
    Note:
        Years are inferred from the experiment.
    '''
    models: list[cmip6.Models] = field(
        default_factory=cmip6.Models.to_list())
    experiment: cmip6.Experiments = cmip6.Experiments.HISTORICAL

    location: tuple[int, int, int, int] = field(default=(1, 0, 0, 1))# [N, W, S, E]

    variable: cmip6.Variables = cmip6.Variables.TEMP
    time_step: str = cmip6.TemporalResolutions.MONTHLY
    months: tuple[str, ...] = cmip6.MONTHS
    days: None|tuple[str, ...] = None

    def __post_init__(self):
        if self.time_step == cmip6.TemporalResolutions.FIXED:
            raise NotImplementedError('Fixed resolution not yet implemented.')
        if self.days is not None and self.time_step != cmip6.TemporalResolutions.DAILY:
            raise ValueError('Days are only valid for daily resolution.')
        self.years: tuple[str,...] = cmip6.default_years(self.experiment)
        self.successes: list[cmip6.Models] = []
        self.failures: list[cmip6.Models] = []

@dataclass
class CMIP6:
    '''
    Copernicus CMIP6 data request.
    
        multiple: models, experiments.
        single: location, variable, time_step(i.e., temporal resolution), months, days.
    
    Note:
        Years are not set but will be inferred from the experiments.
    '''
    models: list[cmip6.Models] = field(
        default_factory=cmip6.Models.to_list())
    experiments: list[cmip6.Experiments] = field(
        default_factory=cmip6.Experiments.to_list())

    location: tuple[int, int, int, int] = field(default=(1, 0, 0, 1)) # [N, W, S, E]

    variable: cmip6.Variables = cmip6.Variables.TEMP
    time_step: str = cmip6.TemporalResolutions.MONTHLY
    months: tuple[str, ...] = cmip6.MONTHS
    days: None|tuple[str, ...] = None

    def __post_init__(self):
        if self.time_step == cmip6.TemporalResolutions.FIXED:
            raise NotImplementedError('Fixed resolution not yet implemented.')
        if self.days is not None and self.time_step != cmip6.TemporalResolutions.DAILY:
            raise ValueError('Days are only valid for daily resolution.')
        self.successes: dict[cmip6.Experiments, list[cmip6.Models]] = {}
        self.failures: dict[cmip6.Experiments, list[cmip6.Models]] = {}

# @dataclass
# class CMIP6FileRequest:
#     '''
#     CDS CMIP6 request protocol.
#     '''
#     variable: cmip6.VariableData = cmip6.VariableData()
#     model: CMIP6Models = CMIP6Models.ACCESS_CM2
#     experiment: CMIP6Experiments = CMIP6Experiments.HISTORICAL
#     model_variable: CMIP6ModelVariable = CMIP6ModelVariable()
#     # temporal_resolution: CMIP6Resolutions = CMIP6Resolutions.MONTHLY
#     # variable: CMIP6Variables = CMIP6Variables.TEMP
#     # area: tuple[int, int, int, int] = (1, 0, 0, 1) # [N, W, S, E]

#     # years: tuple[str, ...] = HISTORY_YEARS
#     # months: tuple[str, ...] = MONTHS
#     # days: None|tuple[str, ...]=None

#     def __post_init__(self):
#         # if self.temporal_resolution == CMIP6Resolutions.DAILY and self.days is None:
#         #     raise ValueError('Error: days must be set for daily requests.')
#         self.dataset = CMIP6_DATASET
#         self.file_chain: list[str] = []
#         self.status = Status.UNPROCESSED
#         self.request: dict[str, any] = self._get_request()

#     def _get_request(self) -> dict[str, any]:
#         '''Returns a dictionary request.'''
#         request = {
#             "area": self.model_variable.area,
#             "year": self.model_variable.years,
#             "month": self.model_variable.months,
#             "model": self.model.value,
#             "experiment": self.experiment.value,
#             "variable": self.model_variable.variable.value,
#             "temporal_resolution": self.model_variable.temporal_resolution.value,
#         }
#         if self.model_variable.temporal_resolution == CMIP6Resolutions.DAILY:
#             request['day'] = self.model_variable.days
#         return request

#     def _create_file_name(self, suffix: str) -> str:
#         '''Returns a file name.'''
#         def date(start: bool) -> str:
#             i = 0 if start else -1
#             return (f'''{self.model_variable.years[i]}{self.model_variable.months[i]}
#                     {self.model_variable.days[i] if self.model_variable.days else ""}''')
#         return f'{self.model.value}_{self.experiment.value}_{date(True)}-{date(False)}{suffix}'

#     def _process_file_name(self, file_name: str,
#                            extension: CMIP6FileFormats) -> str:
#         '''Checks if the file name is valid.'''
#         if file_name:
#             elements = file_name.split('.')
#             if len(elements) > 1 and f'.{elements[-1]}' == extension.value:
#                 raise ValueError(
#                     f'''File extension conflict:
#                     provided file name ends in {"." + elements[-1]},
#                     but requested file type is {extension.value}.'''
#                 )
#             return file_name
#         else:
#             return self._create_file_name(extension.value)

#     def _check_path(self, path: Path, overwrite: bool = False) -> None:
#         '''Checks if the file path exists and if it should be overwritten.'''
#         if path.exists() and not overwrite:
#             raise FileExistsError(
#                 f'File: {str(path)} exists, overwritting not requested, exiting request.')
#         if not path.exists() and not path.parents[0].exists():
#             raise FileNotFoundError(
#                 f'File directory: {str(path.parents[0])} does not exist, exiting request.')

#     def unzip(self, zippath: Path, name: str, extension: CMIP6FileFormats) -> None:
#         '''Unzips a file.'''
#         new_name = zippath.parent / name
#         try:
#             with zipfile.ZipFile(zippath, 'r') as zip_ref:
#                 files = [f for f in zip_ref.namelist() if f.endswith(extension.value)]
#                 if len(files) != 1:
#                     raise FileNotFoundError(
#                         f'''Error: expected one {extension.value} file,
#                         found {len(files)} in {zippath}.''')
#                 zip_ref.extract(files[0], zippath.parent)
#                 (zippath.parent / Path(files[0]).name).rename(new_name)
#                 self.file_chain.append(new_name)
#         except Exception as e:
#             self.status = Status.ERROR
#             return f'Error: {e}. Failed to unzip {zippath}.'

#     def send_request(self, directory: str,
#                      file_name: str = '',
#                      file_type: CMIP6FileFormats = CMIP6FileFormats.NETCDF,
#                      overwrite: bool = False) -> str:
#         '''Attempts to retrieve data from the Copernicus CDS.'''
#         # even though a file type is requested,
#         # the service puts it inside a zip file.
#         name = self._process_file_name(file_name,
#                                        CMIP6FileFormats.ZIP)
#         self.request['format'] = file_type.value
#         zippath = Path(directory) / name
#         self._check_path(zippath, overwrite)
#         try:
#             # retrieve data from CDS.
#             client = cdsapi.Client()
#             client.retrieve(self.dataset, self.request, zippath) #.download()
#             self.file_chain.append(zippath)
#             print(f'Successfully downloaded to: {zippath}')
#             # unzip data file retrieved from CDS.
#             datafilepath = self._process_file_name(file_name, file_type)
#             self.unzip(zippath, datafilepath, file_type)
#             print(f'Unzipped data at: {datafilepath}')
#             self.status = Status.SUCCESS
#             return self.file_chain[-1]
#         except Exception as e:
#             self.status = Status.ERROR
#             return f'Error: {e}'

# class CMIP6ExperimentRequest:
#     '''Request all models for a specific CMIP6 experiment.'''
#     #models: tuple[CMIP6Models, ...] = CMIP6Models.to_list()
#     experiment: CMIP6Experiments = CMIP6Experiments.HISTORICAL

# def cmip6_directory(base_directory: str,
#                     variable: str, resolution: str) -> str:
#     '''
#     Creates directory for CMIP6 data, at:
#         base_directory/cmip6/variable/resolution/zip

#     '''
#     path = Path(base_directory)
#     if not path.exists():
#         raise FileNotFoundError(f'Error: {path} does not exist.')
#     # cmip6/variable/resolution (e.g. cmip6/tas/monthly)
#     for part in ('cmip6', variable, resolution):
#         path = path / part
#         if not path.exists():
#             print(f'creating: {path}')
#             path.mkdir()
#     return path

# def name_cmip6_file(model: str, yrs: tuple[str,...], ext: str = '.nc',
#                     mos: str|tuple[str, ...] = '', days: str|tuple[str,...] = '') -> str:
#     '''Creates a CMIP6 file name.'''
#     def yyyymmdd(yr: str, mo: None|str, day: None|str) -> str:
#         return f'{yr}{mo if mo else ""}{day if day else ""}'
#     dates = f'{yyyymmdd({yrs[0]}, mos[0], days[0])}-{yyyymmdd({yrs[-1]}, mos[-1], days[-1])}'
#     return f'{model}_{dates}{ext}'

# def check_path(path: Path, overwrite: bool = False) -> str:
#     '''Checks if the file path exists and if it should be overwritten.'''
#     if path.exists():
#         if not overwrite:
#             return f'{str(path)} already exists, exiting request.'
#     else:
#         if not path.parents[0].exists():
#             return f'File directory: {str(path.parents[0])} does not exist, exiting request.'
#     return ''

# def small_monthly_request(
#     dataset: str,
#     experiment: str, model: str,
#     variable: str,
#     months: tuple[str, ...], years: tuple[str, ...],
#     area: list[int], file_path: str, overwrite: bool = False) -> str:
#     '''Makes a small monthly request.'''
#     path = Path(file_path)
#     if (msg := check_path(path, overwrite)) != '':
#         return msg
#     request = {
#         "temporal_resolution": "monthly",
#         "experiment": experiment, "variable": variable, "model": model,
#         "month": months, "year": years, "area": area,
#         "format": FILE_FORMATS[path.suffix]
#     }
#     try:
#         client = cdsapi.Client()
#         client.retrieve(dataset, request, file_path).download()
#     except Exception as e:
#         return f'Error: {e}'
#     return f'downloaded {file_path}'

# request = {
#     "temporal_resolution": "daily",
#     "experiment": "historical",
#     "variable": "precipitation",
#     "model": "access_cm2",
#     "month": [
#         "01", "02", "03",
#         "04", "05", "06",
#         "07", "08", "09",
#         "10", "11", "12"
#     ],
#     "day": [
#         "01", "02", "03",
#         "04", "05", "06",
#         "07", "08", "09",
#         "10", "11", "12",
#         "13", "14", "15",
#         "16", "17", "18",
#         "19", "20", "21",
#         "22", "23", "24",
#         "25", "26", "27",
#         "28", "29", "30",
#         "31"
#     ],
#     "year": [
#         "1850", "1851", "1852",
#         "1853", "1854", "1855",
#         "1856", "1857", "1858",
#         "1859", "1860", "1861",
#         "1862", "1863", "1864",
#         "1865", "1866", "1867",
#         "1868", "1869", "1870",
#         "1871", "1872", "1873",
#         "1874", "1875", "1876",
#         "1877", "1878", "1879",
#         "1880", "1881", "1882",
#         "1883", "1884", "1885",
#         "1886", "1887", "1888",
#         "1889", "1890", "1891",
#         "1892", "1893", "1894",
#         "1895", "1896", "1897",
#         "1898", "1899", "1900",
#         "1901", "1902", "1903",
#         "1904", "1905", "1906",
#         "1907", "1908", "1909",
#         "1910", "1911", "1912",
#         "1913", "1914", "1915",
#         "1916", "1917", "1918",
#         "1919", "1920", "1921",
#         "1922", "1923", "1924",
#         "1925", "1926", "1927",
#         "1928", "1929", "1930",
#         "1931", "1932", "1933",
#         "1934", "1935", "1936",
#         "1937", "1938", "1939",
#         "1940", "1941", "1942",
#         "1943", "1944", "1945",
#         "1946", "1947", "1948",
#         "1949", "1950", "1951",
#         "1952", "1953", "1954",
#         "1955", "1956", "1957",
#         "1958", "1959", "1960",
#         "1961", "1962", "1963",
#         "1964", "1965", "1966",
#         "1967", "1968", "1969",
#         "1970", "1971", "1972",
#         "1973", "1974", "1975",
#         "1976", "1977", "1978",
#         "1979", "1980", "1981",
#         "1982", "1983", "1984",
#         "1985", "1986", "1987",
#         "1988", "1989", "1990",
#         "1991", "1992", "1993",
#         "1994", "1995", "1996",
#         "1997", "1998", "1999",
#         "2000", "2001", "2002",
#         "2003", "2004", "2005",
#         "2006", "2007", "2008",
#         "2009", "2010", "2011",
#         "2012", "2013", "2014"
#     ],
#     "area": [23, 100, 13, 108]
# }

# client = cdsapi.Client()
# client.retrieve(dataset, request).download()

# months = [
#     "01", "02", "03",
#     "04", "05", "06",
#     "07", "08", "09",
#     "10", "11", "12"
# ]
# days = [
#     "01", "02", "03",
#     "04", "05", "06",
#     "07", "08", "09",
#     "10", "11", "12",
#     "13", "14", "15",
#     "16", "17", "18",
#     "19", "20", "21",
#     "22", "23", "24",
#     "25", "26", "27",
#     "28", "29", "30",
#     "31"
# ]
# history_yrs = [
#     "1850", "1851", "1852",
#     "1853", "1854", "1855",
#     "1856", "1857", "1858",
#     "1859", "1860", "1861",
#     "1862", "1863", "1864",
#     "1865", "1866", "1867",
#     "1868", "1869", "1870",
#     "1871", "1872", "1873",
#     "1874", "1875", "1876",
#     "1877", "1878", "1879",
#     "1880", "1881", "1882",
#     "1883", "1884", "1885",
#     "1886", "1887", "1888",
#     "1889", "1890", "1891",
#     "1892", "1893", "1894",
#     "1895", "1896", "1897",
#     "1898", "1899", "1900",
#     "1901", "1902", "1903",
#     "1904", "1905", "1906",
#     "1907", "1908", "1909",
#     "1910", "1911", "1912",
#     "1913", "1914", "1915",
#     "1916", "1917", "1918",
#     "1919", "1920", "1921",
#     "1922", "1923", "1924",
#     "1925", "1926", "1927",
#     "1928", "1929", "1930",
#     "1931", "1932", "1933",
#     "1934", "1935", "1936",
#     "1937", "1938", "1939",
#     "1940", "1941", "1942",
#     "1943", "1944", "1945",
#     "1946", "1947", "1948",
#     "1949", "1950", "1951",
#     "1952", "1953", "1954",
#     "1955", "1956", "1957",
#     "1958", "1959", "1960",
#     "1961", "1962", "1963",
#     "1964", "1965", "1966",
#     "1967", "1968", "1969",
#     "1970", "1971", "1972",
#     "1973", "1974", "1975",
#     "1976", "1977", "1978",
#     "1979", "1980", "1981",
#     "1982", "1983", "1984",
#     "1985", "1986", "1987",
#     "1988", "1989", "1990",
#     "1991", "1992", "1993",
#     "1994", "1995", "1996",
#     "1997", "1998", "1999",
#     "2000", "2001", "2002",
#     "2003", "2004", "2005",
#     "2006", "2007", "2008",
#     "2009", "2010", "2011",
#     "2012", "2013", "2014"
# ]
