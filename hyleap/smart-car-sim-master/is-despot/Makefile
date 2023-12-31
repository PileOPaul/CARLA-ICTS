##### Directories

SRCDIR = src
INCDIR = include
OBJDIR = build
DEPDIR = .deps

##### Compiler/linker options

INCL = -I $(INCDIR) -I $(SRCDIR)
CXX = g++
CXXFLAGS = -pthread -O3 -g -c -Wall -Wno-sign-compare -fpic $(INCL) $(GPROF)
LDFLAGS = -O3 -g -Wno-sign-compare -dynamiclib $(GPROF)

##### Files

VPATH = $(shell find -L $(INCDIR) $(SRCDIR) -type d \( ! -name '.*' \))
SOURCES = $(shell find -L $(SRCDIR) -name '*.cpp')
OBJS = $(addprefix $(OBJDIR)/, $(notdir $(SOURCES:.cpp=.o)))
DEPS = $(addprefix $(DEPDIR)/, $(notdir $(SOURCES:.cpp=.d)))
PROBLEMS = $(addprefix problems/, $(shell ls problems))

##### Targets

.PHONY: core directory library problems clean pomdpx

core: directory $(DEPS) $(OBJS) pomdpx

pomdpx:
	make -C pomdpx

# Rule for creating directories needed for build
directory:
	@mkdir -p $(OBJDIR) $(DEPDIR)

# Rules for generating dependencies
$(DEPDIR)/%.d: %.cpp
	@mkdir -p $(DEPDIR); \
	$(CXX) -MM $(CXXFLAGS) $< > $@; \
	sed -ie 's;\(.*\)\.o:;$(OBJDIR)/\1.o $(DEPDIR)/\1.d:;g' $@

# Include generated dependencies
-include $(DEPS)

# Rules for creating object files
$(OBJDIR)/%.o: %.cpp
	$(CXX) $(CXXFLAGS) $< -o $@ 

# Rules for creating library from the object files
lib: $(OBJS)
	$(CXX) $(OBJS) $(LDFLAGS) -I $(INCDIR) -o $(OBJDIR)/libdespot.so

# Rules for compiling the executables for problems
problems:
	$(foreach var, $(PROBLEMS), make -C $(var);)

# Rule for installing the library
install:
	sudo cp -r build/libdespot.so usr/lib/
	sudo cp -r include /usr/include/despot

# Rules for repository cleaning
clean:
	rm -rf $(OBJDIR) $(DEPDIR)
